"""Atomic quota-controlled task creation.

Direct tasks take over an upload's retention window. Derived processing tasks share
the analysis input and inherit its expiry without extending or mutating the source.
"""

import logging
import os
import secrets
import shutil
import sqlite3
import stat
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator, NoReturn, TypeVar

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    DailyUsage,
    ProcessingStyle,
    Task,
    TaskStatus,
    TaskType,
    Upload,
    UploadStatus,
    TaskEvent,
)
from app.filesystem import open_directory_fd
from app.usage.service import hash_identity, utc_date

ResultT = TypeVar("ResultT")
SQLITE_RETRY_DELAYS_SECONDS = (0.01, 0.025, 0.05)
FREE_RETRY_ERROR_CODES = {
    "restart_interrupted",
    "resource_error",
    "resource_exhausted",
    "timeout",
    "task_timeout",
    "task_store",
    "task_store_busy",
    "system_error",
}
logger = logging.getLogger(__name__)
CLEANUP_PLAN_VERSION = 1
RMTREE_AVOIDS_SYMLINK_ATTACKS = shutil.rmtree.avoids_symlink_attacks


class TaskCreationError(Exception):
    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.retryable = retryable


class TaskService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.settings = settings
        self.clock = clock

    def create_analysis(
        self,
        upload_id: str,
        client_id: str,
        request_ip: str,
    ) -> Task:
        return self._run_atomic(
            lambda now, client_hash, ip_hash: self._create_from_upload(
                upload_id,
                TaskType.ANALYSIS,
                None,
                now,
                client_hash,
                ip_hash,
            ),
            client_id,
            request_ip,
        )

    def create_processing(
        self,
        *,
        upload_id: str | None,
        source_task_id: str | None,
        style: ProcessingStyle,
        client_id: str,
        request_ip: str,
    ) -> Task:
        if upload_id is not None:
            return self._run_atomic(
                lambda now, client_hash, ip_hash: self._create_from_upload(
                    upload_id,
                    TaskType.PROCESSING,
                    style,
                    now,
                    client_hash,
                    ip_hash,
                ),
                client_id,
                request_ip,
            )
        if source_task_id is None:
            raise ValueError("source_task_id is required")
        return self._run_atomic(
            lambda now, client_hash, ip_hash: self._create_from_task(
                source_task_id,
                style,
                now,
                client_hash,
                ip_hash,
            ),
            client_id,
            request_ip,
        )

    def get_owned(
        self,
        task_id: str,
        client_id: str,
        request_ip: str,
    ) -> Task:
        task = self._owned_task(
            task_id,
            hash_identity(client_id),
            hash_identity(request_ip),
        )
        if task.status is TaskStatus.EXPIRED and task.error_code != "user_deleted":
            raise TaskCreationError(410, "task_expired", "The task has expired.")
        return task

    def cancel(
        self,
        task_id: str,
        client_id: str,
        request_ip: str,
    ) -> tuple[Task, bool]:
        return self._run_atomic(
            lambda now, client_hash, ip_hash: self._cancel(
                task_id,
                now,
                client_hash,
                ip_hash,
            ),
            client_id,
            request_ip,
        )

    def retry(
        self,
        task_id: str,
        client_id: str,
        request_ip: str,
    ) -> Task:
        return self._run_atomic(
            lambda now, client_hash, ip_hash: self._retry(
                task_id,
                now,
                client_hash,
                ip_hash,
            ),
            client_id,
            request_ip,
        )

    def delete(
        self,
        task_id: str,
        client_id: str,
        request_ip: str,
    ) -> tuple[Task, bool]:
        try:
            task, pending = self._run_atomic(
                lambda now, client_hash, ip_hash: self._delete(
                    task_id,
                    now,
                    client_hash,
                    ip_hash,
                ),
                client_id,
                request_ip,
            )
        except TaskCreationError:
            raise
        except SQLAlchemyError as exc:
            self._raise_cleanup_store_failure(task_id, exc)
        if not pending:
            task = self.finalize_delete(task.id)
        return task, pending

    def finalize_delete(self, task_id: str) -> Task:
        try:
            plan = self._ensure_cleanup_plan(task_id)
            if plan is None:
                task = self.session.get(Task, task_id)
                if task is None:
                    raise TaskCreationError(404, "task_not_found", "The task was not found.")
                return task
            self._perform_filesystem_cleanup(plan)
            return self._finalize_cleanup_phase_b(task_id, plan)
        except OSError:
            diagnostic_id = secrets.token_hex(12)
            logger.exception(
                "Task cleanup failed for %s; diagnostic_id=%s",
                task_id,
                diagnostic_id,
            )
            try:
                return self._record_cleanup_failure(task_id, diagnostic_id)
            except SQLAlchemyError as exc:
                self._raise_cleanup_store_failure(task_id, exc)
        except SQLAlchemyError as exc:
            self._raise_cleanup_store_failure(task_id, exc)

    def _raise_cleanup_store_failure(
        self,
        task_id: str,
        error: SQLAlchemyError,
    ) -> NoReturn:
        self.session.rollback()
        self.session.expire_all()
        diagnostic_id = secrets.token_hex(12)
        logger.exception(
            "Task cleanup database failure for %s; diagnostic_id=%s",
            task_id,
            diagnostic_id,
        )
        if isinstance(error, OperationalError) and self._is_sqlite_contention(error):
            raise self._task_store_busy() from error
        raise self._task_store_unavailable() from error

    def _ensure_cleanup_plan(self, task_id: str) -> dict[str, object] | None:
        self.session.rollback()
        self.session.expire_all()
        try:
            if self.session.get_bind().dialect.name == "sqlite":
                self.session.execute(text("BEGIN IMMEDIATE"))
            task = self.session.get(Task, task_id)
            if task is None:
                raise TaskCreationError(404, "task_not_found", "The task was not found.")
            if not task.cleanup_pending:
                self.session.commit()
                return None
            if task.cleanup_plan is None:
                task.cleanup_plan = self._cleanup_plan(task, self.clock())
            plan = self._validated_cleanup_plan(task.cleanup_plan)
            current_plan = self._cleanup_plan(task, self.clock())
            if plan["source_file"] is not None and current_plan["source_file"] is None:
                plan["source_file"] = None
            if task.cleanup_plan != plan:
                task.cleanup_plan = plan
            task.status = TaskStatus.EXPIRED
            task.stage = None
            task.progress = 0
            self.session.commit()
            return plan
        except BaseException:
            self.session.rollback()
            self.session.expire_all()
            raise

    def _finalize_cleanup_phase_b(
        self,
        task_id: str,
        plan: dict[str, object],
    ) -> Task:
        self.session.rollback()
        self.session.expire_all()
        try:
            if self.session.get_bind().dialect.name == "sqlite":
                self.session.execute(text("BEGIN IMMEDIATE"))
            task = self.session.get(Task, task_id)
            if task is None:
                raise TaskCreationError(404, "task_not_found", "The task was not found.")
            if (
                not task.cleanup_pending
                or task.cleanup_plan != plan
            ):
                raise TaskCreationError(
                    409,
                    "task_deleting",
                    "The task deletion state changed. Retry the request.",
                    retryable=True,
                )
            now = self.clock()
            terminal_error_code = str(plan["reason"])
            self.session.execute(delete(TaskEvent).where(TaskEvent.task_id == task.id))
            self._delete_claimed_upload_for_removed_source(task, plan)
            task.status = TaskStatus.EXPIRED
            task.stage = None
            task.progress = 0
            task.error_code = terminal_error_code
            task.error_message = None
            task.retryable = False
            task.result_manifest = None
            task.input_path = None
            task.upload_id = None
            task.source_task_id = None
            task.style = None
            task.selected_hdu = None
            task.cancel_requested_at = None
            task.cleanup_pending = False
            task.cleanup_error = None
            task.cleanup_plan = None
            task.finished_at = task.finished_at or now
            task.expires_at = now
            self.session.commit()
            return task
        except BaseException:
            self.session.rollback()
            self.session.expire_all()
            raise

    def _record_cleanup_failure(self, task_id: str, diagnostic_id: str) -> Task:
        self.session.rollback()
        self.session.expire_all()
        if self.session.get_bind().dialect.name == "sqlite":
            self.session.execute(text("BEGIN IMMEDIATE"))
        task = self.session.get(Task, task_id)
        if task is None:
            self.session.rollback()
            raise TaskCreationError(404, "task_not_found", "The task was not found.")
        task.cleanup_pending = True
        task.cleanup_error = f"diagnostic_id={diagnostic_id}"
        self.session.commit()
        return task

    def _delete_claimed_upload_for_removed_source(
        self,
        task: Task,
        plan: dict[str, object],
    ) -> None:
        validated = self._validated_cleanup_plan(plan)
        if validated["source_file"] is None or task.input_path is None:
            return
        upload = (
            self.session.get(Upload, task.upload_id)
            if task.upload_id is not None
            else None
        )
        if upload is None:
            upload = self.session.scalar(
                select(Upload).where(Upload.stored_path == task.input_path)
            )
        if upload is None or upload.claimed_at is None:
            return
        self.session.execute(
            update(Task)
            .where(Task.upload_id == upload.id)
            .values(upload_id=None)
        )
        task.upload_id = None
        self.session.flush()
        self.session.delete(upload)

    def _cleanup_plan(
        self,
        task: Task,
        now: datetime,
        *,
        reason: str | None = None,
    ) -> dict[str, object]:
        cleanup_reason = reason or (
            "user_deleted"
            if task.delete_requested_at is not None or task.error_code == "user_deleted"
            else "task_expired"
        )
        if cleanup_reason not in {"task_expired", "user_deleted"}:
            raise ValueError("cleanup reason is invalid")
        task_dir = self._validated_components(("tasks", task.id))
        source_file = self._relative_components(task.input_path)
        if source_file is not None and task.input_path is not None:
            remaining_references = self.session.scalar(
                select(func.count())
                .select_from(Task)
                .where(
                    Task.id != task.id,
                    Task.input_path == task.input_path,
                    Task.status != TaskStatus.EXPIRED,
                    or_(
                        Task.status.in_(
                            [
                                TaskStatus.QUEUED,
                                TaskStatus.RUNNING,
                                TaskStatus.CANCELLING,
                            ]
                        ),
                        Task.expires_at.is_(None),
                        Task.expires_at > now,
                    ),
                )
            )
            if remaining_references != 0:
                source_file = None
        return {
            "version": CLEANUP_PLAN_VERSION,
            "reason": cleanup_reason,
            "task_dir": list(task_dir),
            "source_file": list(source_file) if source_file is not None else None,
        }

    def _relative_components(self, value: str | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        root = Path(os.path.abspath(self.settings.data_root))
        candidate = Path(os.path.abspath(value))
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return None
        return self._validated_components(relative.parts)

    @staticmethod
    def _validated_components(components: object) -> tuple[str, ...]:
        if not isinstance(components, (list, tuple)) or not components:
            raise OSError("cleanup path is invalid")
        validated: list[str] = []
        for component in components:
            if (
                not isinstance(component, str)
                or not component
                or component in {".", ".."}
                or "/" in component
                or "\\" in component
                or "\x00" in component
            ):
                raise OSError("cleanup path is invalid")
            validated.append(component)
        return tuple(validated)

    def _validated_cleanup_plan(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict) or value.get("version") != CLEANUP_PLAN_VERSION:
            raise OSError("cleanup plan is invalid")
        reason = value.get("reason", "user_deleted")
        if reason not in {"task_expired", "user_deleted"}:
            raise OSError("cleanup plan is invalid")
        task_dir = self._validated_components(value.get("task_dir"))
        raw_source = value.get("source_file")
        source_file = (
            self._validated_components(raw_source) if raw_source is not None else None
        )
        return {
            "version": CLEANUP_PLAN_VERSION,
            "reason": reason,
            "task_dir": list(task_dir),
            "source_file": list(source_file) if source_file is not None else None,
        }

    def _perform_filesystem_cleanup(self, plan: dict[str, object]) -> None:
        validated = self._validated_cleanup_plan(plan)
        with self._open_data_root() as root_fd:
            self._remove_tree(
                root_fd,
                self._validated_components(validated["task_dir"]),
            )
            source = validated["source_file"]
            if source is not None:
                self._unlink_regular_file(
                    root_fd,
                    self._validated_components(source),
                )

    @contextmanager
    def _open_data_root(self) -> Iterator[int]:
        path = Path(os.path.abspath(self.settings.data_root))
        current_fd = open_directory_fd(path)
        try:
            yield current_fd
        finally:
            os.close(current_fd)

    @contextmanager
    def _open_parent(
        self,
        root_fd: int,
        components: tuple[str, ...],
    ) -> Iterator[tuple[int, str]]:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current_fd = os.dup(root_fd)
        try:
            for component in components[:-1]:
                next_fd = os.open(component, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            yield current_fd, components[-1]
        finally:
            os.close(current_fd)

    def _remove_tree(self, root_fd: int, components: tuple[str, ...]) -> None:
        if not RMTREE_AVOIDS_SYMLINK_ATTACKS:
            raise OSError("safe recursive cleanup is unavailable")
        try:
            with self._open_parent(root_fd, components) as (parent_fd, name):
                metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise OSError("cleanup target is not a directory")
                shutil.rmtree(name, dir_fd=parent_fd)
        except FileNotFoundError:
            return

    def _unlink_regular_file(self, root_fd: int, components: tuple[str, ...]) -> None:
        try:
            with self._open_parent(root_fd, components) as (parent_fd, name):
                metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode):
                    raise OSError("cleanup source is not a regular file")
                os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError:
            return

    def _run_atomic(
        self,
        operation: Callable[[datetime, str, str], ResultT],
        client_id: str,
        request_ip: str,
    ) -> ResultT:
        client_hash = hash_identity(client_id)
        ip_hash = hash_identity(request_ip)
        for attempt in range(len(SQLITE_RETRY_DELAYS_SECONDS) + 1):
            self.session.rollback()
            self.session.expire_all()
            try:
                if self.session.get_bind().dialect.name == "sqlite":
                    self.session.execute(text("BEGIN IMMEDIATE"))
                result = operation(self.clock(), client_hash, ip_hash)
                self.session.commit()
                return result
            except OperationalError as exc:
                self.session.rollback()
                self.session.expire_all()
                if not self._is_sqlite_contention(exc):
                    raise
                if attempt == len(SQLITE_RETRY_DELAYS_SECONDS):
                    raise self._task_store_busy() from exc
                time.sleep(SQLITE_RETRY_DELAYS_SECONDS[attempt])
            except BaseException:
                self.session.rollback()
                self.session.expire_all()
                raise
        raise AssertionError("unreachable")

    def _create_from_upload(
        self,
        upload_id: str,
        task_type: TaskType,
        style: ProcessingStyle | None,
        now: datetime,
        client_hash: str,
        ip_hash: str,
    ) -> Task:
        upload = self._owned_upload(upload_id, client_hash, ip_hash)
        self._validate_upload(upload, now)
        task_expires_at = now + timedelta(seconds=self.settings.task_ttl_seconds)
        claimed_upload_id = self.session.execute(
            update(Upload)
            .where(
                Upload.id == upload.id,
                Upload.client_id_hash == client_hash,
                Upload.ip_hash == ip_hash,
                Upload.status == UploadStatus.READY,
                Upload.claimed_at.is_(None),
                Upload.expires_at > now,
            )
            .values(claimed_at=now, expires_at=task_expires_at)
            .returning(Upload.id)
        ).scalar_one_or_none()
        if claimed_upload_id is None:
            self.session.expire(upload)
            self._validate_upload(upload, now)
            raise self._upload_already_claimed()

        self._charge_usage(now, client_hash, ip_hash)
        task = Task(
            id=secrets.token_urlsafe(24),
            type=task_type,
            status=TaskStatus.QUEUED,
            client_id_hash=client_hash,
            ip_hash=ip_hash,
            upload_id=upload.id,
            style=style,
            selected_hdu=upload.selected_hdu,
            input_path=upload.stored_path,
            quota_charged=True,
            created_at=now,
            expires_at=task_expires_at,
        )
        self.session.add(task)
        return task

    def _create_from_task(
        self,
        source_task_id: str,
        style: ProcessingStyle,
        now: datetime,
        client_hash: str,
        ip_hash: str,
    ) -> Task:
        source = self.session.scalar(
            select(Task).where(
                Task.id == source_task_id,
                Task.client_id_hash == client_hash,
                Task.ip_hash == ip_hash,
            )
        )
        if source is None:
            raise self._source_task_invalid()
        if source.delete_requested_at is not None or source.cleanup_pending:
            raise self._source_task_invalid()
        if source.status is TaskStatus.EXPIRED:
            raise self._source_file_expired()
        if source.type is not TaskType.ANALYSIS or source.status is not TaskStatus.COMPLETED:
            raise self._source_task_invalid()
        if source.expires_at is None or source.expires_at <= now:
            raise self._source_file_expired()
        if source.input_path is None or not Path(source.input_path).is_file():
            raise self._source_file_expired()

        self._charge_usage(now, client_hash, ip_hash)
        task = Task(
            id=secrets.token_urlsafe(24),
            type=TaskType.PROCESSING,
            status=TaskStatus.QUEUED,
            client_id_hash=client_hash,
            ip_hash=ip_hash,
            source_task_id=source.id,
            style=style,
            selected_hdu=source.selected_hdu,
            input_path=source.input_path,
            quota_charged=True,
            created_at=now,
            expires_at=source.expires_at,
        )
        self.session.add(task)
        return task

    def _cancel(
        self,
        task_id: str,
        now: datetime,
        client_hash: str,
        ip_hash: str,
    ) -> tuple[Task, bool]:
        task = self._owned_task(task_id, client_hash, ip_hash)
        notify = task.status in {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.CANCELLING,
        }
        if task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            task.status = TaskStatus.CANCELLING
            task.stage = "cancelling"
            task.cancel_requested_at = now
        return task, notify

    def _retry(
        self,
        task_id: str,
        now: datetime,
        client_hash: str,
        ip_hash: str,
    ) -> Task:
        source = self._owned_task(task_id, client_hash, ip_hash)
        if source.delete_requested_at is not None or source.cleanup_pending:
            if source.status is TaskStatus.EXPIRED:
                raise TaskCreationError(410, "task_expired", "The task has expired.")
            raise TaskCreationError(
                409,
                "task_deleting",
                "The task is being deleted.",
            )
        if (
            source.status is TaskStatus.EXPIRED
            or source.expires_at is None
            or source.expires_at <= now
            or source.input_path is None
            or not Path(source.input_path).is_file()
        ):
            raise self._source_file_expired()

        free_retry = (
            source.quota_charged
            and not source.free_retry_used
            and source.status is TaskStatus.FAILED
            and source.error_code in FREE_RETRY_ERROR_CODES
        )
        if free_retry:
            source.free_retry_used = True
        else:
            self._charge_usage(now, client_hash, ip_hash)

        task = Task(
            id=secrets.token_urlsafe(24),
            type=source.type,
            status=TaskStatus.QUEUED,
            client_id_hash=client_hash,
            ip_hash=ip_hash,
            upload_id=source.upload_id,
            source_task_id=source.source_task_id,
            style=source.style,
            selected_hdu=source.selected_hdu,
            input_path=source.input_path,
            quota_charged=not free_retry,
            created_at=now,
            expires_at=source.expires_at,
        )
        self.session.add(task)
        return task

    def _delete(
        self,
        task_id: str,
        now: datetime,
        client_hash: str,
        ip_hash: str,
    ) -> tuple[Task, bool]:
        task = self._owned_task(task_id, client_hash, ip_hash)
        task.delete_requested_at = task.delete_requested_at or now
        task.cleanup_pending = True
        task.cleanup_error = None
        if task.cleanup_plan is None:
            task.cleanup_plan = self._cleanup_plan(task, now)
        if task.status in {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.CANCELLING,
        }:
            task.status = TaskStatus.CANCELLING
            task.stage = "cancelling"
            task.cancel_requested_at = task.cancel_requested_at or now
            task.error_code = "user_deleted"
            task.error_message = None
            task.retryable = False
            return task, True

        task.error_code = "user_deleted"
        task.error_message = None
        task.retryable = False
        return task, False

    def _owned_task(
        self,
        task_id: str,
        client_hash: str,
        ip_hash: str,
    ) -> Task:
        task = self.session.scalar(
            select(Task).where(
                Task.id == task_id,
                Task.client_id_hash == client_hash,
                Task.ip_hash == ip_hash,
            )
        )
        if task is None:
            raise TaskCreationError(404, "task_not_found", "The task was not found.")
        return task

    def _owned_upload(
        self,
        upload_id: str,
        client_hash: str,
        ip_hash: str,
    ) -> Upload:
        upload = self.session.scalar(
            select(Upload).where(
                Upload.id == upload_id,
                Upload.client_id_hash == client_hash,
                Upload.ip_hash == ip_hash,
            )
        )
        if upload is None:
            raise TaskCreationError(404, "upload_not_found", "The upload was not found.")
        return upload

    def _validate_upload(self, upload: Upload, now: datetime) -> None:
        if upload.claimed_at is not None:
            raise self._upload_already_claimed()
        if upload.status is not UploadStatus.READY:
            raise TaskCreationError(404, "upload_not_found", "The upload was not found.")
        if upload.expires_at <= now or not Path(upload.stored_path).is_file():
            raise TaskCreationError(410, "upload_expired", "The upload has expired.")

    def _charge_usage(
        self,
        now: datetime,
        client_hash: str,
        ip_hash: str,
    ) -> None:
        if self.settings.daily_task_limit <= 0:
            raise self._daily_limit_reached()
        usage_date = utc_date(now)
        ip_usage_count = self.session.scalar(
            select(func.coalesce(func.sum(DailyUsage.count), 0)).where(
                DailyUsage.date == usage_date,
                DailyUsage.ip_hash == ip_hash,
            )
        )
        if (ip_usage_count or 0) >= self.settings.daily_task_limit:
            raise self._daily_limit_reached()
        usage_count = self.session.execute(
            text(
                """
                INSERT INTO daily_usage (date, client_id_hash, ip_hash, count)
                VALUES (:date, :client_id_hash, :ip_hash, 1)
                ON CONFLICT(date, client_id_hash, ip_hash)
                DO UPDATE SET count = daily_usage.count + 1
                WHERE daily_usage.count < :daily_task_limit
                RETURNING count
                """
            ),
            {
                "date": usage_date.isoformat(),
                "client_id_hash": client_hash,
                "ip_hash": ip_hash,
                "daily_task_limit": self.settings.daily_task_limit,
            },
        ).scalar_one_or_none()
        if usage_count is None:
            raise self._daily_limit_reached()

    @staticmethod
    def _is_sqlite_contention(error: OperationalError) -> bool:
        original = error.orig
        if not isinstance(original, sqlite3.OperationalError):
            return False
        error_code = getattr(original, "sqlite_errorcode", None)
        primary_error_code = error_code & 0xFF if isinstance(error_code, int) else None
        if primary_error_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return True
        message = str(original).lower()
        return "database is locked" in message or "database table is locked" in message

    @staticmethod
    def _upload_already_claimed() -> TaskCreationError:
        return TaskCreationError(
            409,
            "upload_already_claimed",
            "The upload has already been used to create a task.",
        )

    @staticmethod
    def _source_task_invalid() -> TaskCreationError:
        return TaskCreationError(
            409,
            "source_task_invalid",
            "The source task cannot be used for processing.",
        )

    @staticmethod
    def _source_file_expired() -> TaskCreationError:
        return TaskCreationError(
            410,
            "source_file_expired",
            "The source task file is no longer available.",
        )

    @staticmethod
    def _daily_limit_reached() -> TaskCreationError:
        return TaskCreationError(
            429,
            "daily_task_limit_reached",
            "The daily task limit has been reached.",
        )

    @staticmethod
    def _task_store_busy() -> TaskCreationError:
        return TaskCreationError(
            503,
            "task_store_busy",
            "The task store is busy. Please retry shortly.",
            retryable=True,
        )

    @staticmethod
    def _task_store_unavailable() -> TaskCreationError:
        return TaskCreationError(
            503,
            "task_store_unavailable",
            "The task store is temporarily unavailable.",
            retryable=True,
        )
