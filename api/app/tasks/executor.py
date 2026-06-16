import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any, TypeVar, cast

from sqlalchemy import select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import EventLevel, Task, TaskStatus, TaskType
from app.tasks.events import (
    TaskEventService,
    is_transient_sqlite_contention,
    require_sqlite,
)
from app.tasks.service import TaskService

logger = logging.getLogger(__name__)
TERMINAL_TTL = timedelta(hours=24)
DatabaseResult = TypeVar("DatabaseResult")


@dataclass(frozen=True)
class HandlerResult:
    result_manifest: dict[str, Any]


class TaskCancelled(RuntimeError):
    pass


class TaskHandlerError(RuntimeError):
    def __init__(self, error_code: str, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = retryable


TaskHandler = Callable[[str], Awaitable[HandlerResult]]


class SerialTaskExecutor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        handlers: Mapping[TaskType, TaskHandler] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        poll_interval_seconds: float = 0.5,
        infrastructure_backoff_initial_seconds: float = 0.05,
        infrastructure_backoff_max_seconds: float = 1.0,
    ) -> None:
        require_sqlite(session_factory, "SerialTaskExecutor")
        self._session_factory = session_factory
        self._settings = settings
        self._clock = clock
        self._events = TaskEventService(session_factory, clock=clock)
        self._handlers = dict(handlers or self._default_handlers())
        self._poll_interval_seconds = poll_interval_seconds
        self._infrastructure_backoff_initial_seconds = infrastructure_backoff_initial_seconds
        self._infrastructure_backoff_max_seconds = infrastructure_backoff_max_seconds
        self._wake = asyncio.Event()
        self._notify_lock = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_notify = False
        self._stopping = False
        self.worker_task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self.worker_task is not None and not self.worker_task.done()

    @property
    def session_factory(self) -> sessionmaker[Session]:
        return self._session_factory

    def start(self) -> None:
        if self.is_running:
            return
        loop = asyncio.get_running_loop()
        with self._notify_lock:
            self._loop = loop
            pending_notify = self._pending_notify
            self._pending_notify = False
        self._stopping = False
        self.worker_task = asyncio.create_task(self._worker_loop(), name="serial-task-worker")
        if pending_notify:
            loop.call_soon(self._wake.set)

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        worker = self.worker_task
        if worker is None:
            return
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        self.worker_task = None
        with self._notify_lock:
            self._loop = None

    def notify(self) -> None:
        with self._notify_lock:
            loop = self._loop
            if loop is None:
                self._pending_notify = True
                return
        loop.call_soon_threadsafe(self._wake.set)

    async def run_until_idle(self) -> None:
        while task_id := self._lease_next():
            await self._process(task_id)
            if self._delete_cleanup_pending(task_id):
                return

    async def _worker_loop(self) -> None:
        backoff_seconds = self._infrastructure_backoff_initial_seconds
        while not self._stopping:
            try:
                self._wake.clear()
                await self.run_until_idle()
                backoff_seconds = self._infrastructure_backoff_initial_seconds
                try:
                    await asyncio.wait_for(
                        self._wake.wait(),
                        timeout=self._poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Serial task worker iteration failed")
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(
                    backoff_seconds * 2,
                    self._infrastructure_backoff_max_seconds,
                )

    def _lease_next(self) -> str | None:
        with self._session_factory() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            running_task_id = session.scalar(
                select(Task.id).where(Task.status == TaskStatus.RUNNING).limit(1)
            )
            if running_task_id is not None:
                session.rollback()
                return None
            task = session.scalar(
                select(Task)
                .where(Task.status.in_([TaskStatus.QUEUED, TaskStatus.CANCELLING]))
                .order_by(Task.created_at, Task.id)
                .limit(1)
            )
            if task is None:
                session.rollback()
                return None
            if task.status is TaskStatus.QUEUED:
                task.status = TaskStatus.RUNNING
                task.started_at = self._clock()
                task.stage = "starting"
            session.commit()
            return task.id

    async def _process(self, task_id: str) -> None:
        task = self._get_task(task_id)
        if task is None:
            return
        if task.delete_requested_at is not None:
            self._finalize_delete(task_id)
            return
        if self._cancel_requested(task):
            self._finish_cancelled(task_id)
            return
        handler = self._handlers[task.type]
        timeout_seconds = (
            self._settings.analysis_timeout_seconds
            if task.type is TaskType.ANALYSIS
            else self._settings.processing_timeout_seconds
        )
        await self._retry_database_operation(
            lambda: self._events.append(
                task_id,
                EventLevel.INFO,
                "task_started",
                {"task_type": task.type.value},
            ),
            "append task_started event",
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                result = await handler(task_id)
            refreshed = self._get_task(task_id)
            if refreshed is None:
                return
            if refreshed.delete_requested_at is not None:
                await self._retry_database_operation(
                    lambda: self._finalize_delete(task_id),
                    "finalize task deletion",
                )
                return
            if self._cancel_requested(refreshed):
                await self._retry_database_operation(
                    lambda: self._finish_cancelled(task_id),
                    "commit task cancellation",
                )
                return
            await self._retry_database_operation(
                lambda: self._finish_completed(task_id, result),
                "commit task completion",
            )
        except TimeoutError:
            await self._retry_database_operation(
                lambda: self._finish_failed(
                    task_id,
                    "task_timeout",
                    "The task exceeded its execution time limit.",
                    retryable=True,
                    event_type="task_timeout",
                ),
                "commit task timeout",
            )
        except TaskCancelled:
            await self._retry_database_operation(
                lambda: self._finish_cancelled(task_id),
                "commit task cancellation",
            )
        except TaskHandlerError as exc:
            error_code = exc.error_code
            error_message = exc.message
            retryable = exc.retryable
            await self._retry_database_operation(
                lambda: self._finish_failed(
                    task_id,
                    error_code,
                    error_message,
                    retryable=retryable,
                    event_type="task_failed",
                ),
                "commit task failure",
            )
        except asyncio.CancelledError:
            raise
        except SQLAlchemyError:
            raise
        except Exception:
            diagnostic_id = secrets.token_hex(12)
            logger.exception("Task %s failed; diagnostic_id=%s", task_id, diagnostic_id)
            await self._retry_database_operation(
                lambda: self._finish_failed(
                    task_id,
                    "system_error",
                    f"diagnostic_id={diagnostic_id}",
                    retryable=True,
                    event_type="task_failed",
                    payload={"diagnostic_id": diagnostic_id},
                ),
                "commit system task failure",
            )

    def _finish_completed(self, task_id: str, result: HandlerResult) -> None:
        now = self._clock()
        with self._session_factory() as session:
            try:
                transition = cast(
                    CursorResult[Any],
                    session.execute(
                        update(Task)
                        .where(
                            Task.id == task_id,
                            Task.status == TaskStatus.RUNNING,
                            Task.cancel_requested_at.is_(None),
                        )
                        .values(
                            status=TaskStatus.COMPLETED,
                            stage="completed",
                            progress=100,
                            result_manifest=result.result_manifest,
                            error_code=None,
                            error_message=None,
                            retryable=False,
                            finished_at=now,
                            expires_at=now + TERMINAL_TTL,
                        )
                    )
                )
                if transition.rowcount == 1:
                    self._events.append_in_session(
                        session,
                        task_id,
                        EventLevel.INFO,
                        "task_completed",
                        {"progress": 100},
                    )
                else:
                    delete_requested = self._cancel_in_session_if_requested(
                        session,
                        task_id,
                        now,
                    )
                session.commit()
                if transition.rowcount != 1 and delete_requested:
                    self._finalize_delete(task_id)
            except BaseException:
                session.rollback()
                raise

    def _finish_cancelled(self, task_id: str) -> None:
        now = self._clock()
        with self._session_factory() as session:
            try:
                delete_requested = self._cancel_in_session_if_requested(
                    session,
                    task_id,
                    now,
                )
                session.commit()
                if delete_requested:
                    self._finalize_delete(task_id)
            except BaseException:
                session.rollback()
                raise

    def _finish_failed(
        self,
        task_id: str,
        error_code: str,
        error_message: str,
        *,
        retryable: bool,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = self._clock()
        with self._session_factory() as session:
            try:
                transition = cast(
                    CursorResult[Any],
                    session.execute(
                        update(Task)
                        .where(
                            Task.id == task_id,
                            Task.status == TaskStatus.RUNNING,
                            Task.cancel_requested_at.is_(None),
                        )
                        .values(
                            status=TaskStatus.FAILED,
                            stage="failed",
                            error_code=error_code,
                            error_message=error_message,
                            retryable=retryable,
                            finished_at=now,
                            expires_at=now + TERMINAL_TTL,
                        )
                    )
                )
                if transition.rowcount == 1:
                    self._events.append_in_session(
                        session,
                        task_id,
                        EventLevel.ERROR,
                        event_type,
                        {
                            "error_code": error_code,
                            "message": error_message,
                            "retryable": retryable,
                            **(payload or {}),
                        },
                    )
                else:
                    delete_requested = self._cancel_in_session_if_requested(
                        session,
                        task_id,
                        now,
                    )
                session.commit()
                if transition.rowcount != 1 and delete_requested:
                    self._finalize_delete(task_id)
            except BaseException:
                session.rollback()
                raise

    def _cancel_in_session_if_requested(
        self,
        session: Session,
        task_id: str,
        now: datetime,
    ) -> bool:
        task = session.get(Task, task_id)
        if task is not None and task.delete_requested_at is not None:
            return True
        transition = cast(
            CursorResult[Any],
            session.execute(
                update(Task)
                .where(
                    Task.id == task_id,
                    Task.status.in_([TaskStatus.RUNNING, TaskStatus.CANCELLING]),
                    (
                        (Task.status == TaskStatus.CANCELLING)
                        | Task.cancel_requested_at.is_not(None)
                    ),
                )
                .values(
                    status=TaskStatus.CANCELLED,
                    stage="cancelled",
                    error_code="user_cancelled",
                    error_message=None,
                    retryable=False,
                    finished_at=now,
                    expires_at=now + TERMINAL_TTL,
                )
            )
        )
        if transition.rowcount == 1:
            self._events.append_in_session(
                session,
                task_id,
                EventLevel.INFO,
                "task_cancelled",
                {},
            )
        return False

    def _finalize_delete(self, task_id: str) -> None:
        with self._session_factory() as session:
            TaskService(
                session,
                self._settings,
                clock=self._clock,
            ).finalize_delete(task_id)

    async def _retry_database_operation(
        self,
        operation: Callable[[], DatabaseResult],
        description: str,
    ) -> DatabaseResult:
        backoff_seconds = self._infrastructure_backoff_initial_seconds
        while True:
            try:
                return operation()
            except asyncio.CancelledError:
                raise
            except OperationalError as exc:
                if not is_transient_sqlite_contention(exc):
                    raise
                logger.exception("Task database operation failed: %s", description)
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(
                    backoff_seconds * 2,
                    self._infrastructure_backoff_max_seconds,
                )

    def _get_task(self, task_id: str) -> Task | None:
        with self._session_factory() as session:
            return session.get(Task, task_id)

    def _delete_cleanup_pending(self, task_id: str) -> bool:
        task = self._get_task(task_id)
        return bool(
            task is not None
            and task.delete_requested_at is not None
            and task.cleanup_pending
        )

    @staticmethod
    def _cancel_requested(task: Task) -> bool:
        return task.cancel_requested_at is not None or task.status is TaskStatus.CANCELLING

    def _default_handlers(self) -> Mapping[TaskType, TaskHandler]:
        from app.tasks.handlers import AnalysisTaskHandler, ProcessingTaskHandler

        return {
            TaskType.ANALYSIS: AnalysisTaskHandler(
                self._session_factory,
                self._settings,
                clock=self._clock,
            ).run,
            TaskType.PROCESSING: ProcessingTaskHandler(
                self._session_factory,
                self._settings,
                clock=self._clock,
            ).run,
        }
