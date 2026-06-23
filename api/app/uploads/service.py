import hashlib
import logging
import os
import secrets
import shutil
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Upload, UploadStatus
from app.fits.errors import FitsInspectionError
from app.fits.inspector import inspect_image
from app.fits.schemas import FitsInspection
from app.filesystem import (
    create_regular_file_fd,
    open_directory_fd,
    open_relative_directory_fd,
    unlink_regular_file_at,
    write_all,
)
from app.uploads.errors import (
    UnexpectedUploadError,
    UploadError,
    insufficient_storage_error,
    unsupported_extension_error,
    upload_too_large_error,
)

CHUNK_SIZE = 1024 * 1024
MAX_ORIGINAL_FILENAME_LENGTH = 255
SUPPORTED_EXTENSIONS = {".fits", ".fit", ".fts", ".xisf"}

Inspector = Callable[[Path], FitsInspection]
DiskUsage = Callable[[Path], shutil._ntuple_diskusage]
Clock = Callable[[], datetime]
logger = logging.getLogger(__name__)


def get_settings() -> Settings:
    return Settings()


def get_inspector() -> Inspector:
    return inspect_image


def get_disk_usage() -> DiskUsage:
    return shutil.disk_usage


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_filename(filename: str | None) -> str:
    basename = (filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    if len(basename) <= MAX_ORIGINAL_FILENAME_LENGTH:
        return basename
    suffix = Path(basename).suffix
    stem_limit = MAX_ORIGINAL_FILENAME_LENGTH - len(suffix)
    return f"{basename[:stem_limit]}{suffix}"


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


class UploadService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        inspector: Inspector = inspect_image,
        disk_usage: DiskUsage = shutil.disk_usage,
        clock: Clock = _utc_now,
    ) -> None:
        self.session = session
        self.settings = settings
        self.inspector = inspector
        self.disk_usage = disk_usage
        self.clock = clock

    async def create(
        self,
        file: UploadFile,
        client_id: str,
        request_ip: str,
        *,
        declared_size_bytes: int | None = None,
    ) -> tuple[Upload, FitsInspection]:
        upload: Upload | None = None
        upload_dir: Path | None = None
        try:
            extension = Path(file.filename or "").suffix.lower()
            if extension not in SUPPORTED_EXTENSIONS:
                raise unsupported_extension_error()

            if (
                declared_size_bytes is not None
                and declared_size_bytes > self.settings.max_upload_bytes
            ):
                raise upload_too_large_error()

            initial_reservation = (
                declared_size_bytes
                if declared_size_bytes is not None and declared_size_bytes >= 0
                else min(self.settings.max_upload_bytes, CHUNK_SIZE)
            )
            self._ensure_disk_space(initial_reservation)

            now = self.clock()
            upload_id = secrets.token_urlsafe(24)
            upload_dir = self.settings.data_root / "uploads" / upload_id
            stored_path = upload_dir / f"input{extension}"
            upload = Upload(
                id=upload_id,
                client_id_hash=_hash(client_id),
                ip_hash=_hash(request_ip),
                original_file_name=_safe_filename(file.filename),
                stored_path=str(stored_path),
                size_bytes=0,
                status=UploadStatus.UPLOADING,
                validation_result=None,
                selected_hdu=None,
                created_at=now,
                expires_at=now + timedelta(seconds=self.settings.upload_ttl_seconds),
            )
            self.session.add(upload)
            self.session.commit()

            data_root_fd = open_directory_fd(self.settings.data_root, create=True)
            try:
                upload_dir_fd = open_relative_directory_fd(
                    data_root_fd,
                    ("uploads", upload_id),
                    create=True,
                )
                try:
                    size_bytes = await self._stream(
                        file,
                        upload_dir_fd,
                        stored_path.name,
                    )
                finally:
                    os.close(upload_dir_fd)
            finally:
                os.close(data_root_fd)

            upload.size_bytes = size_bytes
            upload.status = UploadStatus.VALIDATING
            self.session.commit()

            inspection = self.inspector(stored_path)
            upload.status = UploadStatus.READY
            upload.validation_result = inspection.model_dump(mode="json")
            upload.selected_hdu = inspection.selected_hdu.index
            upload.expires_at = self.clock() + timedelta(hours=1)
            self.session.commit()
            return upload, inspection
        except FitsInspectionError as exc:
            if upload is not None:
                self._invalidate_and_cleanup(upload, upload_dir, exc.error_code)
            raise UploadError(422, exc.error_code, str(exc)) from exc
        except UploadError as exc:
            if upload is not None:
                self._invalidate_and_cleanup(upload, upload_dir, exc.error_code)
            raise
        except BaseException as exc:
            if upload is not None:
                self._invalidate_and_cleanup(upload, upload_dir, "internal_error")
            if isinstance(exc, Exception):
                raise UnexpectedUploadError() from exc
            raise
        finally:
            await file.close()

    async def _stream(
        self,
        file: UploadFile,
        upload_dir_fd: int,
        stored_name: str,
    ) -> int:
        size_bytes = 0
        output_fd = create_regular_file_fd(
            upload_dir_fd,
            stored_name,
            exclusive=True,
        )
        try:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                next_size = size_bytes + len(chunk)
                if next_size > self.settings.max_upload_bytes:
                    raise upload_too_large_error()
                write_all(output_fd, chunk)
                size_bytes = next_size
                self._ensure_disk_space(0)
            os.fsync(output_fd)
        except BaseException:
            os.close(output_fd)
            output_fd = -1
            unlink_regular_file_at(upload_dir_fd, stored_name)
            raise
        finally:
            if output_fd >= 0:
                os.close(output_fd)
        return size_bytes

    def _ensure_disk_space(self, reservation_bytes: int) -> None:
        disk_path = _nearest_existing_parent(self.settings.data_root)
        free_bytes = self.disk_usage(disk_path).free
        required = self.settings.min_free_disk_bytes + reservation_bytes
        if free_bytes < required:
            raise insufficient_storage_error()

    def _invalidate_and_cleanup(
        self,
        upload: Upload,
        upload_dir: Path | None,
        error_code: str,
    ) -> None:
        validation_result: dict[str, object] = {"error_code": error_code}

        def mark_invalid(row: Upload) -> None:
            row.status = UploadStatus.INVALID
            row.validation_result = validation_result
            row.selected_hdu = None
            row.cleanup_pending = True
            row.cleanup_error = None
            row.cleanup_plan = {
                "version": 1,
                "upload_dir": ["uploads", row.id],
            }

        try:
            if not self._persist_invalid(upload, mark_invalid):
                return
            cleanup_result = self._remove_upload_dir(upload_dir, upload.id)
            if cleanup_result is not None:
                upload.validation_result = {**validation_result, **cleanup_result}
                upload.cleanup_error = (
                    f"diagnostic_id={cleanup_result['cleanup_diagnostic_id']}"
                )
                self.session.commit()
            else:
                upload.cleanup_pending = False
                upload.cleanup_error = None
                upload.cleanup_plan = None
                self.session.commit()
        except Exception:
            logger.exception("Failed to finalize invalid upload cleanup")
            self.session.rollback()

    def _persist_invalid(
        self,
        upload: Upload,
        mark_invalid: Callable[[Upload], None],
    ) -> bool:
        try:
            mark_invalid(upload)
            self.session.commit()
            return True
        except Exception:
            logger.exception("Failed to persist terminal upload state; retrying")
            self.session.rollback()
            try:
                recovered = self.session.get(Upload, upload.id)
                if recovered is None:
                    recovered = self.session.merge(upload)
                mark_invalid(recovered)
                self.session.commit()
                return True
            except Exception:
                logger.exception("Failed to persist terminal upload state after rollback")
                self.session.rollback()
                return False

    def _remove_upload_dir(
        self,
        upload_dir: Path | None,
        upload_id: str,
    ) -> dict[str, object] | None:
        if upload_dir is None:
            return None
        try:
            data_root_fd = open_directory_fd(self.settings.data_root)
            try:
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | os.O_DIRECTORY
                    | os.O_NOFOLLOW
                )
                uploads_fd = os.open("uploads", flags, dir_fd=data_root_fd)
                try:
                    metadata = os.stat(
                        upload_id,
                        dir_fd=uploads_fd,
                        follow_symlinks=False,
                    )
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise OSError("upload cleanup target is not a directory")
                    if not shutil.rmtree.avoids_symlink_attacks:
                        raise OSError("safe recursive cleanup is unavailable")
                    shutil.rmtree(upload_id, dir_fd=uploads_fd)
                finally:
                    os.close(uploads_fd)
            finally:
                os.close(data_root_fd)
        except FileNotFoundError:
            return None
        except Exception:
            diagnostic_id = secrets.token_urlsafe(12)
            logger.exception(
                "Upload cleanup failed; diagnostic_id=%s",
                diagnostic_id,
            )
            return {
                "cleanup_pending": True,
                "cleanup_diagnostic_id": diagnostic_id,
            }
        return None
