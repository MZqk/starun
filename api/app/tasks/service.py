"""Atomic quota-controlled task creation.

Direct tasks take over an upload's retention window. Derived processing tasks share
the analysis input and inherit its expiry without extending or mutating the source.
"""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    ProcessingStyle,
    Task,
    TaskStatus,
    TaskType,
    Upload,
    UploadStatus,
)
from app.usage.service import hash_identity, utc_date


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
        return self._create_from_upload(
            upload_id,
            TaskType.ANALYSIS,
            None,
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
            return self._create_from_upload(
                upload_id,
                TaskType.PROCESSING,
                style,
                client_id,
                request_ip,
            )
        if source_task_id is None:
            raise ValueError("source_task_id is required")
        return self._create_from_task(source_task_id, style, client_id, request_ip)

    def _create_from_upload(
        self,
        upload_id: str,
        task_type: TaskType,
        style: ProcessingStyle | None,
        client_id: str,
        request_ip: str,
    ) -> Task:
        now = self.clock()
        client_hash = hash_identity(client_id)
        ip_hash = hash_identity(request_ip)
        try:
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
            self.session.commit()
            return task
        except BaseException:
            self.session.rollback()
            raise

    def _create_from_task(
        self,
        source_task_id: str,
        style: ProcessingStyle,
        client_id: str,
        request_ip: str,
    ) -> Task:
        now = self.clock()
        client_hash = hash_identity(client_id)
        ip_hash = hash_identity(request_ip)
        try:
            source = self.session.scalar(
                select(Task).where(
                    Task.id == source_task_id,
                    Task.client_id_hash == client_hash,
                    Task.ip_hash == ip_hash,
                )
            )
            if source is None:
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
            self.session.commit()
            return task
        except BaseException:
            self.session.rollback()
            raise

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
                "date": utc_date(now).isoformat(),
                "client_id_hash": client_hash,
                "ip_hash": ip_hash,
                "daily_task_limit": self.settings.daily_task_limit,
            },
        ).scalar_one_or_none()
        if usage_count is None:
            raise self._daily_limit_reached()

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
