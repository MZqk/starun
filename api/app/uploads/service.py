import hashlib
import secrets
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Upload, UploadStatus
from app.fits.errors import FitsInspectionError
from app.fits.inspector import inspect_fits
from app.fits.schemas import FitsInspection
from app.uploads.errors import (
    UnexpectedUploadError,
    UploadError,
    insufficient_storage_error,
    unsupported_extension_error,
    upload_too_large_error,
)

CHUNK_SIZE = 1024 * 1024
MAX_ORIGINAL_FILENAME_LENGTH = 255
SUPPORTED_EXTENSIONS = {".fits", ".fit", ".fts"}

Inspector = Callable[[Path], FitsInspection]
DiskUsage = Callable[[Path], shutil._ntuple_diskusage]
Clock = Callable[[], datetime]


def get_settings() -> Settings:
    return Settings()


def get_inspector() -> Inspector:
    return inspect_fits


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
        inspector: Inspector = inspect_fits,
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

            if (
                declared_size_bytes is not None
                and declared_size_bytes > self.settings.max_upload_bytes
            ):
                raise upload_too_large_error()

            upload_dir.mkdir(parents=True)
            size_bytes = await self._stream(file, stored_path)

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
                self._invalidate(upload, exc.error_code)
                self._remove_upload_dir(upload_dir)
            raise UploadError(422, exc.error_code, str(exc)) from exc
        except UploadError as exc:
            if upload is not None:
                self._invalidate(upload, exc.error_code)
                self._remove_upload_dir(upload_dir)
            raise
        except BaseException as exc:
            if upload is not None:
                self._invalidate(upload, "internal_error")
                self._remove_upload_dir(upload_dir)
            if isinstance(exc, Exception):
                raise UnexpectedUploadError() from exc
            raise
        finally:
            await file.close()

    async def _stream(self, file: UploadFile, stored_path: Path) -> int:
        size_bytes = 0
        with stored_path.open("xb") as output:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                next_size = size_bytes + len(chunk)
                if next_size > self.settings.max_upload_bytes:
                    raise upload_too_large_error()
                self._ensure_disk_space(len(chunk))
                output.write(chunk)
                size_bytes = next_size
        return size_bytes

    def _ensure_disk_space(self, reservation_bytes: int) -> None:
        disk_path = _nearest_existing_parent(self.settings.data_root)
        free_bytes = self.disk_usage(disk_path).free
        required = self.settings.min_free_disk_bytes + reservation_bytes
        if free_bytes < required:
            raise insufficient_storage_error()

    def _invalidate(self, upload: Upload, error_code: str) -> None:
        try:
            upload.status = UploadStatus.INVALID
            upload.validation_result = {"error_code": error_code}
            upload.selected_hdu = None
            self.session.commit()
        except Exception:
            self.session.rollback()

    @staticmethod
    def _remove_upload_dir(upload_dir: Path | None) -> None:
        if upload_dir is not None:
            shutil.rmtree(upload_dir, ignore_errors=True)
