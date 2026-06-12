import hashlib
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.config import Settings
from app.db.models import DailyUsage, Task, Upload, UploadStatus
from app.uploads.errors import UploadError
from app.uploads.service import (
    CHUNK_SIZE,
    UploadService,
    get_disk_usage,
    get_inspector,
)
from app.main import app
from tests.fixtures.fits_factory import (
    make_corrupt_fits,
    make_fits,
    make_table_only_fits,
)

DiskUsage = namedtuple("DiskUsage", "total used free")


def upload(
    client: TestClient,
    path: Path,
    *,
    headers: dict[str, str] | None = None,
    filename: str | None = None,
) -> Any:
    with path.open("rb") as handle:
        return client.post(
            "/api/uploads",
            headers=headers,
            files={"file": (filename or path.name, handle, "application/octet-stream")},
        )


def assert_error(response: Any, status: int, error_code: str) -> None:
    assert response.status_code == status
    assert response.json() == {
        "error_code": error_code,
        "message": response.json()["message"],
        "retryable": False,
        "quota_charged": False,
        "diagnostic_id": response.json()["diagnostic_id"],
    }


def assert_no_usage_or_tasks(session: Session) -> None:
    assert session.scalar(select(func.count()).select_from(Task)) == 0
    assert session.scalar(select(func.count()).select_from(DailyUsage)) == 0


def test_valid_fits_is_streamed_inspected_and_persisted_ready(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    data_root: Path,
    tmp_path: Path,
) -> None:
    source = make_fits(
        tmp_path,
        np.zeros((4, 4), dtype=np.float32),
        [np.ones((8, 8), dtype=np.float32)],
    )

    response = upload(client, source, headers=headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ready"
    assert body["inspection"]["selected_hdu"]["index"] == 1
    assert body["expires_at"].endswith("Z")
    row = db_session.get(Upload, body["upload_id"])
    assert row is not None
    assert row.status is UploadStatus.READY
    assert row.selected_hdu == 1
    assert row.validation_result == body["inspection"]
    assert Path(row.stored_path).is_file()
    assert Path(row.stored_path).parent == data_root / "uploads" / row.id
    assert_no_usage_or_tasks(db_session)


@pytest.mark.parametrize(
    ("factory", "error_code"),
    [
        (make_table_only_fits, "unsupported_fits_data"),
        (make_corrupt_fits, "invalid_fits"),
    ],
)
def test_invalid_fits_returns_422_retains_audit_and_removes_files(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    data_root: Path,
    tmp_path: Path,
    factory: Any,
    error_code: str,
) -> None:
    response = upload(client, factory(tmp_path), headers=headers)

    assert_error(response, 422, error_code)
    row = db_session.scalar(select(Upload))
    assert row is not None
    assert row.status is UploadStatus.INVALID
    assert row.validation_result == {"error_code": error_code}
    assert not (data_root / "uploads" / row.id).exists()
    assert_no_usage_or_tasks(db_session)


def test_bad_extension_is_rejected_before_row_or_file(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    data_root: Path,
    tmp_path: Path,
) -> None:
    path = tmp_path / "image.txt"
    path.write_bytes(b"not fits")

    response = upload(client, path, headers=headers)

    assert_error(response, 415, "unsupported_file_extension")
    assert db_session.scalar(select(func.count()).select_from(Upload)) == 0
    assert list((data_root / "uploads").glob("**/*")) == []


def test_exact_max_is_accepted_and_max_plus_one_is_cleaned_up(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    tmp_path: Path,
) -> None:
    source = make_fits(tmp_path, np.ones((2, 2), dtype=np.int16))
    settings.max_upload_bytes = source.stat().st_size
    accepted = upload(client, source, headers=headers)
    assert accepted.status_code == 201

    settings.max_upload_bytes = source.stat().st_size - 1
    rejected = upload(client, source, headers=headers)
    assert_error(rejected, 413, "upload_too_large")
    rows = db_session.scalars(select(Upload).order_by(Upload.created_at)).all()
    assert [row.status for row in rows] == [UploadStatus.READY, UploadStatus.INVALID]
    assert not Path(rows[1].stored_path).parent.exists()


class RecordingStream(BytesIO):
    def __init__(self, value: bytes, *, fail_after: int | None = None) -> None:
        super().__init__(value)
        self.read_sizes: list[int] = []
        self.bytes_read = 0
        self.fail_after = fail_after
        self.closed_by_service = False

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if self.fail_after is not None and self.tell() >= self.fail_after:
            raise ConnectionError("client disconnected")
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed_by_service = True
        super().close()


@pytest.mark.asyncio
async def test_service_reads_only_bounded_chunks_and_closes_upload(
    settings: Settings,
    db_session: Session,
    tmp_path: Path,
) -> None:
    source = make_fits(tmp_path, np.ones((2, 2), dtype=np.int16))
    stream = RecordingStream(source.read_bytes())
    file = UploadFile(stream, filename="image.fits")

    await UploadService(db_session, settings).create(file, "client", "127.0.0.1")

    assert stream.read_sizes
    assert max(stream.read_sizes) == CHUNK_SIZE
    assert -1 not in stream.read_sizes
    assert stream.closed_by_service is True


@pytest.mark.asyncio
async def test_service_enforces_max_without_content_length_and_cleans_up(
    settings: Settings,
    db_session: Session,
    data_root: Path,
) -> None:
    settings.max_upload_bytes = CHUNK_SIZE
    stream = RecordingStream(b"x" * (settings.max_upload_bytes + 1))
    file = UploadFile(stream, filename="image.fits")

    with pytest.raises(UploadError) as exc_info:
        await UploadService(db_session, settings).create(
            file,
            "client",
            "127.0.0.1",
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.error_code == "upload_too_large"
    assert stream.bytes_read == settings.max_upload_bytes + 1
    assert stream.read_sizes == [CHUNK_SIZE, CHUNK_SIZE]
    assert stream.closed_by_service is True
    row = db_session.scalar(select(Upload))
    assert row is not None
    assert row.status is UploadStatus.INVALID
    assert row.validation_result == {"error_code": "upload_too_large"}
    assert not (data_root / "uploads" / row.id).exists()


def test_client_and_ip_are_hashed_without_persisting_originals(
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
) -> None:
    client_id = "private-client-id"
    response = upload(
        client,
        make_fits(tmp_path, np.ones((2, 2), dtype=np.int16)),
        headers={"X-Starun-Client-Id": client_id},
    )

    assert response.status_code == 201
    row = db_session.scalar(select(Upload))
    assert row is not None
    assert row.client_id_hash == hashlib.sha256(client_id.encode()).hexdigest()
    assert row.ip_hash == hashlib.sha256("testclient".encode()).hexdigest()
    assert client_id not in str(row.__dict__)
    assert "testclient" not in str(row.__dict__)
    assert row.client_id_hash not in response.text
    assert row.ip_hash not in response.text


@pytest.mark.asyncio
async def test_ready_expiry_is_one_hour_after_validation_completion(
    settings: Settings,
    db_session: Session,
    tmp_path: Path,
) -> None:
    upload_started_at = datetime(2026, 6, 12, 8, 0, tzinfo=UTC)
    validation_completed_at = upload_started_at + timedelta(minutes=15)
    times = iter([upload_started_at, validation_completed_at])
    source = make_fits(tmp_path, np.ones((2, 2), dtype=np.int16))
    file = UploadFile(source.open("rb"), filename="image.fits")

    row, _inspection = await UploadService(
        db_session,
        settings,
        clock=lambda: next(times),
    ).create(
        file,
        "client",
        "127.0.0.1",
    )

    assert row.status is UploadStatus.READY
    assert row.created_at.tzinfo is not None
    assert row.expires_at.tzinfo is not None
    assert row.created_at == upload_started_at
    assert row.expires_at == validation_completed_at + timedelta(hours=1)


def test_disk_low_before_upload_returns_507_without_row(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    tmp_path: Path,
) -> None:
    settings.min_free_disk_bytes = 100
    app.dependency_overrides[get_disk_usage] = lambda: lambda _path: DiskUsage(100, 99, 1)
    try:
        response = upload(
            client,
            make_fits(tmp_path, np.ones((2, 2), dtype=np.int16)),
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_disk_usage, None)

    assert_error(response, 507, "insufficient_storage")
    assert db_session.scalar(select(func.count()).select_from(Upload)) == 0


@pytest.mark.asyncio
async def test_declared_file_size_is_included_in_initial_disk_requirement(
    settings: Settings,
    db_session: Session,
) -> None:
    declared_size_bytes = 2 * CHUNK_SIZE
    settings.max_upload_bytes = 3 * CHUNK_SIZE
    settings.min_free_disk_bytes = 100

    def disk_usage(_path: Path) -> Any:
        return DiskUsage(
            4 * CHUNK_SIZE,
            0,
            settings.min_free_disk_bytes + declared_size_bytes - 1,
        )

    file = UploadFile(BytesIO(b"small"), filename="image.fits")

    with pytest.raises(UploadError) as exc_info:
        await UploadService(db_session, settings, disk_usage=disk_usage).create(
            file,
            "client",
            "127.0.0.1",
            declared_size_bytes=declared_size_bytes,
        )

    assert exc_info.value.status_code == 507
    assert exc_info.value.error_code == "insufficient_storage"
    assert db_session.scalar(select(func.count()).select_from(Upload)) == 0


def test_disk_low_during_upload_cleans_partial_file_and_retains_audit(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    data_root: Path,
    tmp_path: Path,
) -> None:
    settings.max_upload_bytes = 10 * CHUNK_SIZE
    settings.min_free_disk_bytes = 100
    values = iter(
        [
            DiskUsage(2 * CHUNK_SIZE, 0, 2 * CHUNK_SIZE),
            DiskUsage(2 * CHUNK_SIZE, 2 * CHUNK_SIZE - 50, 50),
        ]
    )
    app.dependency_overrides[get_disk_usage] = lambda: lambda _path: next(values)
    try:
        response = upload(
            client,
            make_fits(tmp_path, np.ones((2, 2), dtype=np.int16)),
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_disk_usage, None)

    assert_error(response, 507, "insufficient_storage")
    row = db_session.scalar(select(Upload))
    assert row is not None and row.status is UploadStatus.INVALID
    assert not (data_root / "uploads" / row.id).exists()


def test_unexpected_inspector_exception_returns_safe_500_and_cleans_up(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    data_root: Path,
    tmp_path: Path,
) -> None:
    def broken_inspector(_path: Path) -> Any:
        raise RuntimeError("/secret/internal/path")

    app.dependency_overrides[get_inspector] = lambda: broken_inspector
    try:
        response = upload(
            client,
            make_fits(tmp_path, np.ones((2, 2), dtype=np.int16)),
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_inspector, None)

    assert_error(response, 500, "internal_error")
    assert response.json()["diagnostic_id"]
    assert "/secret/internal/path" not in response.text
    row = db_session.scalar(select(Upload))
    assert row is not None and row.status is UploadStatus.INVALID
    assert not (data_root / "uploads" / row.id).exists()


def test_filename_traversal_is_bounded_metadata_only(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    data_root: Path,
    tmp_path: Path,
) -> None:
    source = make_fits(tmp_path, np.ones((2, 2), dtype=np.int16))
    malicious_name = "../../" + ("x" * 400) + ".FITS"

    response = upload(client, source, headers=headers, filename=malicious_name)

    assert response.status_code == 201
    row = db_session.get(Upload, response.json()["upload_id"])
    assert row is not None
    assert "/" not in row.original_file_name
    assert "\\" not in row.original_file_name
    assert len(row.original_file_name) <= 255
    assert Path(row.stored_path).parent == data_root / "uploads" / row.id
    assert Path(row.stored_path).name == "input.fits"


def test_missing_client_header_is_400(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed_filenames: list[str | None] = []
    original_close = StarletteUploadFile.close

    async def recording_close(file: StarletteUploadFile) -> None:
        closed_filenames.append(file.filename)
        await original_close(file)

    monkeypatch.setattr(StarletteUploadFile, "close", recording_close)
    response = upload(
        client,
        make_fits(tmp_path, np.ones((2, 2), dtype=np.int16)),
    )

    assert_error(response, 400, "missing_client_id")
    assert closed_filenames == ["image.fits", "image.fits"]


@pytest.mark.asyncio
async def test_read_exception_cleans_files_marks_invalid_and_closes_upload(
    settings: Settings,
    db_session: Session,
) -> None:
    stream = RecordingStream(b"x" * (CHUNK_SIZE + 1), fail_after=CHUNK_SIZE)
    file = UploadFile(stream, filename="image.fits")

    with pytest.raises(Exception) as exc_info:
        await UploadService(db_session, settings).create(file, "client", "127.0.0.1")

    assert "client disconnected" not in str(exc_info.value)
    row = db_session.scalar(select(Upload))
    assert row is not None and row.status is UploadStatus.INVALID
    assert not Path(row.stored_path).parent.exists()
    assert stream.closed_by_service is True
