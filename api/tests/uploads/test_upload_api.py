import hashlib
import json
import tempfile
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.formparsers import MultiPartParser
from starlette.types import Message, Receive, Scope, Send
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.config import Settings
from app.db.models import DailyUsage, Task, Upload, UploadStatus
from app.uploads.errors import UploadError, upload_too_large_error
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


def assert_error(
    response: Any,
    status: int,
    error_code: str,
    *,
    retryable: bool = False,
) -> None:
    assert response.status_code == status
    assert response.json() == {
        "error_code": error_code,
        "message": response.json()["message"],
        "retryable": retryable,
        "quota_charged": False,
        "diagnostic_id": response.json()["diagnostic_id"],
    }


def assert_no_usage_or_tasks(session: Session) -> None:
    assert session.scalar(select(func.count()).select_from(Task)) == 0
    assert session.scalar(select(func.count()).select_from(DailyUsage)) == 0


async def send_chunked_asgi_request(
    body: bytes,
    *,
    chunk_size: int,
) -> tuple[int, dict[str, Any]]:
    messages = [
        {
            "type": "http.request",
            "body": body[offset : offset + chunk_size],
            "more_body": offset + chunk_size < len(body),
        }
        for offset in range(0, len(body), chunk_size)
    ]
    sent: list[Message] = []
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/uploads",
            "raw_path": b"/api/uploads",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"content-type", b"multipart/form-data; boundary=starun-boundary"),
                (b"x-starun-client-id", b"test-client"),
            ],
            "client": ("testclient", 123),
            "server": ("testserver", 80),
        },
    )

    async def receive() -> Message:
        return cast(Message, messages.pop(0))

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, cast(Receive, receive), cast(Send, send))
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return start["status"], cast(dict[str, Any], json.loads(response_body))


def multipart_body(file_bytes: bytes) -> bytes:
    return (
        b"--starun-boundary\r\n"
        b'Content-Disposition: form-data; name="file"; filename="image.fits"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        + file_bytes
        + b"\r\n--starun-boundary--\r\n"
    )


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


def test_exact_max_is_accepted_and_declared_max_plus_one_is_rejected(
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
    assert [row.status for row in rows] == [UploadStatus.READY]


@pytest.mark.asyncio
async def test_chunked_oversized_request_is_rejected_before_route(
    client: TestClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.max_upload_bytes = 16
    route_reached = False

    async def fail_if_route_reached(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal route_reached
        route_reached = True
        raise AssertionError("upload route reached")

    monkeypatch.setattr(UploadService, "create", fail_if_route_reached)
    body = multipart_body(b"x" * (settings.max_upload_bytes + 1024 * 1024 + 1))

    status, response = await send_chunked_asgi_request(body, chunk_size=64 * 1024)

    assert status == 413
    assert response["error_code"] == "upload_too_large"
    assert route_reached is False


@pytest.mark.asyncio
async def test_request_is_rejected_before_parser_when_disk_reserve_is_low(
    client: TestClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.min_free_disk_bytes = 100
    route_reached = False

    async def fail_if_route_reached(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal route_reached
        route_reached = True
        raise AssertionError("upload route reached")

    monkeypatch.setattr(UploadService, "create", fail_if_route_reached)
    app.dependency_overrides[get_disk_usage] = lambda: lambda _path: DiskUsage(100, 99, 1)
    try:
        status, response = await send_chunked_asgi_request(
            multipart_body(b"small"),
            chunk_size=8,
        )
    finally:
        app.dependency_overrides.pop(get_disk_usage, None)

    assert status == 507
    assert response["error_code"] == "insufficient_storage"
    assert response["retryable"] is True
    assert route_reached is False


@pytest.mark.asyncio
async def test_temp_spool_rollover_checks_cumulative_buffered_bytes(
    client: TestClient,
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.max_upload_bytes = 2 * MultiPartParser.spool_max_size
    temp_root = tmp_path / "multipart-temp"
    temp_root.mkdir()
    route_reached = False
    checked_paths: list[Path] = []
    free_bytes = 700 * 1024

    async def fail_if_route_reached(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal route_reached
        route_reached = True
        raise AssertionError("upload route reached")

    def disk_usage(path: Path) -> Any:
        checked_paths.append(path)
        return DiskUsage(4 * MultiPartParser.spool_max_size, 0, free_bytes)

    monkeypatch.setattr(UploadService, "create", fail_if_route_reached)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))
    app.dependency_overrides[get_disk_usage] = lambda: disk_usage
    try:
        status, response = await send_chunked_asgi_request(
            multipart_body(b"x" * (MultiPartParser.spool_max_size + 128 * 1024)),
            chunk_size=600 * 1024,
        )
    finally:
        app.dependency_overrides.pop(get_disk_usage, None)

    assert status == 507
    assert response["error_code"] == "insufficient_storage"
    assert response["retryable"] is True
    assert route_reached is False
    assert checked_paths
    assert set(checked_paths) == {temp_root}


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

    assert_error(response, 507, "insufficient_storage", retryable=True)
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


@pytest.mark.asyncio
async def test_declared_file_size_over_max_is_rejected_before_disk_check(
    settings: Settings,
    db_session: Session,
) -> None:
    settings.max_upload_bytes = 100
    disk_checked = False

    def disk_usage(_path: Path) -> Any:
        nonlocal disk_checked
        disk_checked = True
        return DiskUsage(100, 100, 0)

    file = UploadFile(BytesIO(b"small"), filename="image.fits")

    with pytest.raises(UploadError) as exc_info:
        await UploadService(db_session, settings, disk_usage=disk_usage).create(
            file,
            "client",
            "127.0.0.1",
            declared_size_bytes=settings.max_upload_bytes + 1,
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.error_code == "upload_too_large"
    assert disk_checked is False
    assert db_session.scalar(select(func.count()).select_from(Upload)) == 0


def test_disk_low_while_receiving_is_rejected_before_upload_row(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    data_root: Path,
    tmp_path: Path,
) -> None:
    settings.max_upload_bytes = 10 * CHUNK_SIZE
    settings.min_free_disk_bytes = 100
    check_count = 0

    def disk_usage(_path: Path) -> Any:
        nonlocal check_count
        check_count += 1
        free = 2 * CHUNK_SIZE if check_count == 1 else 50
        return DiskUsage(2 * CHUNK_SIZE, 2 * CHUNK_SIZE - free, free)

    app.dependency_overrides[get_disk_usage] = lambda: disk_usage
    try:
        response = upload(
            client,
            make_fits(tmp_path, np.ones((2, 2), dtype=np.int16)),
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_disk_usage, None)

    assert_error(response, 507, "insufficient_storage", retryable=True)
    assert db_session.scalar(select(func.count()).select_from(Upload)) == 0
    assert list((data_root / "uploads").glob("**/*")) == []


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


@pytest.mark.asyncio
async def test_invalidation_recovers_after_first_terminal_commit_fails(
    settings: Settings,
    db_session: Session,
    tmp_path: Path,
) -> None:
    source = make_fits(tmp_path, np.ones((2, 2), dtype=np.int16))
    file = UploadFile(source.open("rb"), filename="image.fits")
    original_commit = db_session.commit
    commit_count = 0

    def fail_first_invalidation_commit() -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 3:
            raise RuntimeError("controlled invalidation commit failure")
        original_commit()

    def invalid_inspector(_path: Path) -> Any:
        raise upload_too_large_error()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_session, "commit", fail_first_invalidation_commit)
    try:
        with pytest.raises(UploadError):
            await UploadService(
                db_session,
                settings,
                inspector=invalid_inspector,
            ).create(file, "client", "127.0.0.1")
    finally:
        monkeypatch.undo()

    db_session.expire_all()
    row = db_session.scalar(select(Upload))
    assert row is not None
    assert row.status is UploadStatus.INVALID
    assert row.validation_result == {"error_code": "upload_too_large"}


def test_cleanup_failure_keeps_original_error_and_marks_cleanup_pending(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_corrupt_fits(tmp_path)
    logged: list[tuple[str, tuple[Any, ...]]] = []

    def failed_rmtree(_path: Path) -> None:
        raise OSError("controlled cleanup failure")

    def record_exception(message: str, *args: Any, **_kwargs: Any) -> None:
        logged.append((message, args))

    monkeypatch.setattr("app.uploads.service.shutil.rmtree", failed_rmtree)
    monkeypatch.setattr("app.uploads.service.logger.exception", record_exception)

    response = upload(client, source, headers=headers)

    assert_error(response, 422, "invalid_fits")
    row = db_session.scalar(select(Upload))
    assert row is not None
    assert row.status is UploadStatus.INVALID
    assert row.validation_result is not None
    assert row.validation_result["error_code"] == "invalid_fits"
    assert row.validation_result["cleanup_pending"] is True
    assert row.validation_result["cleanup_diagnostic_id"]
    assert logged == [
        (
            "Upload cleanup failed; diagnostic_id=%s",
            (row.validation_result["cleanup_diagnostic_id"],),
        )
    ]


def test_upload_openapi_declares_all_error_responses(client: TestClient) -> None:
    responses = client.get("/openapi.json").json()["paths"]["/api/uploads"]["post"]["responses"]

    assert {"400", "413", "415", "422", "500", "507"} <= responses.keys()
