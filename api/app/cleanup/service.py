import asyncio
import logging
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Task, TaskStatus, Upload, UploadStatus
from app.tasks.service import TaskService

logger = logging.getLogger(__name__)
TERMINAL_TASK_STATUSES = {
    TaskStatus.CANCELLED,
    TaskStatus.COMPLETED,
    TaskStatus.REVIEW_REQUIRED,
    TaskStatus.FAILED,
}
UPLOAD_CLEANUP_PLAN_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(UTC)


def recover_interrupted_uploads(
    session_factory: sessionmaker[Session],
) -> int:
    with session_factory() as session:
        uploads = list(
            session.scalars(
                select(Upload).where(
                    Upload.status.in_(
                        [UploadStatus.UPLOADING, UploadStatus.VALIDATING]
                    )
                )
            )
        )
        for upload in uploads:
            upload.status = UploadStatus.INVALID
            upload.validation_result = {"error_code": "upload_interrupted"}
            upload.selected_hdu = None
            upload.cleanup_pending = True
            upload.cleanup_error = None
            upload.cleanup_plan = {
                "version": UPLOAD_CLEANUP_PLAN_VERSION,
                "upload_dir": ["uploads", upload.id],
            }
        session.commit()
        return len(uploads)


class CleanupService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.session = session
        self.settings = settings
        self.clock = clock

    def run_once(self) -> None:
        now = self.clock()
        self._cleanup_uploads(now)
        self._cleanup_tasks(now)

    def _cleanup_uploads(self, now: datetime) -> None:
        uploads = list(
            self.session.scalars(
                select(Upload).where(
                    or_(
                        Upload.cleanup_pending.is_(True),
                        (
                            (Upload.expires_at <= now)
                            & (
                                (Upload.status == UploadStatus.INVALID)
                                | (
                                    (Upload.status == UploadStatus.READY)
                                    & Upload.claimed_at.is_(None)
                                )
                            )
                        ),
                    ),
                )
            )
        )
        for upload in uploads:
            if not upload.cleanup_pending:
                upload.cleanup_pending = True
                upload.cleanup_error = None
                upload.cleanup_plan = self._upload_cleanup_plan(upload)
                if upload.status is UploadStatus.READY:
                    upload.status = UploadStatus.INVALID
                    upload.validation_result = {"error_code": "upload_expired"}
                    upload.selected_hdu = None
                self.session.commit()
            try:
                self._remove_upload_directory(upload)
            except OSError:
                logger.exception("Could not remove expired upload %s", upload.id)
                upload.cleanup_error = f"diagnostic_id={secrets.token_hex(12)}"
                self.session.commit()
                continue
            self.session.delete(upload)
            self.session.commit()

    def _cleanup_tasks(self, now: datetime) -> None:
        task_ids = list(
            self.session.scalars(
                select(Task.id).where(
                    or_(
                        Task.cleanup_pending.is_(True),
                        (
                            Task.status.in_(TERMINAL_TASK_STATUSES)
                            & Task.expires_at.is_not(None)
                            & (Task.expires_at <= now)
                        ),
                    )
                )
            )
        )
        for task_id in task_ids:
            self._expire_task(task_id, now)

    def _expire_task(self, task_id: str, now: datetime) -> None:
        task = self.session.get(Task, task_id)
        if (
            task is None
            or (
                not task.cleanup_pending
                and (
                    task.status not in TERMINAL_TASK_STATUSES
                    or task.expires_at is None
                    or task.expires_at > now
                )
            )
        ):
            return
        task_service = TaskService(self.session, self.settings, clock=lambda: now)
        cleanup_reason = (
            task.cleanup_plan.get("reason")
            if isinstance(task.cleanup_plan, dict)
            else None
        )
        if (
            cleanup_reason == "user_deleted"
            or task.delete_requested_at is not None
            or task.error_code == "user_deleted"
        ):
            task.delete_requested_at = task.delete_requested_at or now
            task.cleanup_pending = True
            task.error_code = "user_deleted"
            task.error_message = None
            task.retryable = False
            if task.cleanup_plan is None:
                task.cleanup_plan = task_service._cleanup_plan(
                    task,
                    now,
                    reason="user_deleted",
                )
            self.session.commit()
            task_service.finalize_delete(task.id)
            return
        if not task.cleanup_pending:
            task.cleanup_pending = True
            task.cleanup_error = None
            task.cleanup_plan = task_service._cleanup_plan(
                task,
                now,
                reason="task_expired",
            )
            task.status = TaskStatus.EXPIRED
            task.stage = None
            task.progress = 0
            task.error_code = "task_expired"
            task.error_message = None
            task.retryable = False
            self.session.commit()
        task_service.finalize_delete(task.id)

    def _remove_upload_directory(self, upload: Upload) -> None:
        plan = self._validated_upload_cleanup_plan(upload.cleanup_plan)
        task_service = TaskService(self.session, self.settings, clock=self.clock)
        with task_service._open_data_root() as root_fd:
            task_service._remove_tree(
                root_fd,
                task_service._validated_components(plan["upload_dir"]),
            )

    def _upload_cleanup_plan(self, upload: Upload) -> dict[str, object]:
        data_root = Path(os.path.abspath(self.settings.data_root))
        stored_path = Path(os.path.abspath(upload.stored_path))
        try:
            relative = stored_path.relative_to(data_root)
        except ValueError as exc:
            raise OSError("upload cleanup path is outside the data root") from exc
        if (
            len(relative.parts) != 3
            or relative.parts[0] != "uploads"
            or relative.parts[1] != upload.id
        ):
            raise OSError("upload cleanup path is invalid")
        return {
            "version": UPLOAD_CLEANUP_PLAN_VERSION,
            "upload_dir": ["uploads", upload.id],
        }

    @staticmethod
    def _validated_upload_cleanup_plan(value: object) -> dict[str, object]:
        if (
            not isinstance(value, dict)
            or value.get("version") != UPLOAD_CLEANUP_PLAN_VERSION
            or not isinstance(value.get("upload_dir"), list)
        ):
            raise OSError("upload cleanup plan is invalid")
        return value


class CleanupScheduler:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        interval_seconds: float = 60.0,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_requested: asyncio.Event | None = None
        self._worker_future: asyncio.Future[None] | None = None

    def start(self) -> None:
        worker_running = (
            self._worker_future is not None and not self._worker_future.done()
        )
        if not worker_running and (self._task is None or self._task.done()):
            self._stop_requested = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="starun-cleanup")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        if self._stop_requested is not None:
            self._stop_requested.set()
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise
        self._task = None
        self._stop_requested = None

    async def _run(self) -> None:
        stop_requested = self._stop_requested
        if stop_requested is None:
            return
        while not stop_requested.is_set():
            try:
                loop = asyncio.get_running_loop()
                self._worker_future = loop.run_in_executor(None, self._run_once)
                try:
                    await asyncio.shield(self._worker_future)
                except asyncio.CancelledError:
                    await asyncio.shield(self._worker_future)
                    raise
            except Exception:
                logger.exception("Scheduled cleanup failed")
            finally:
                if self._worker_future is not None and self._worker_future.done():
                    self._worker_future = None
            try:
                await asyncio.wait_for(
                    stop_requested.wait(),
                    timeout=self.interval_seconds,
                )
            except TimeoutError:
                pass

    def _run_once(self) -> None:
        with self.session_factory() as session:
            CleanupService(session, self.settings).run_once()
