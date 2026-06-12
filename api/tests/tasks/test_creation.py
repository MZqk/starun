import hashlib
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
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
)
from app.db.session import create_engine_and_session
from app.tasks.service import TaskCreationError, TaskService


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _ready_upload(
    session: Session,
    settings: Settings,
    *,
    upload_id: str,
    client_id: str = "test-client",
    request_ip: str = "testclient",
    expires_at: datetime | None = None,
) -> Upload:
    path = settings.data_root / "uploads" / upload_id / "input.fits"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"SIMPLE FITS INPUT")
    upload = Upload(
        id=upload_id,
        client_id_hash=_hash(client_id),
        ip_hash=_hash(request_ip),
        original_file_name="input.fits",
        stored_path=str(path),
        size_bytes=path.stat().st_size,
        status=UploadStatus.READY,
        validation_result={
            "selected_hdu": {"index": 2},
            "basic_metrics": {"width": 100, "height": 80},
        },
        selected_hdu=2,
        created_at=datetime.now(UTC),
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(upload)
    session.commit()
    return upload


def _completed_analysis(
    session: Session,
    settings: Settings,
    *,
    task_id: str = "analysis-source",
    client_id: str = "test-client",
    request_ip: str = "testclient",
    expires_at: datetime | None = None,
    file_exists: bool = True,
) -> Task:
    upload = _ready_upload(
        session,
        settings,
        upload_id=f"{task_id}-upload",
        client_id=client_id,
        request_ip=request_ip,
    )
    if not file_exists:
        Path(upload.stored_path).unlink()
    task = Task(
        id=task_id,
        type=TaskType.ANALYSIS,
        status=TaskStatus.COMPLETED,
        client_id_hash=upload.client_id_hash,
        ip_hash=upload.ip_hash,
        upload_id=upload.id,
        selected_hdu=upload.selected_hdu,
        input_path=upload.stored_path,
        result_manifest={"inspection": upload.validation_result, "stars": 42},
        quota_charged=True,
        created_at=datetime.now(UTC) - timedelta(minutes=5),
        finished_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=12),
    )
    upload.claimed_at = task.created_at
    session.add(task)
    session.commit()
    return task


def _assert_error(
    response: Any,
    status_code: int,
    error_code: str,
    *,
    retryable: bool = False,
) -> None:
    assert response.status_code == status_code
    assert response.json() == {
        "error_code": error_code,
        "message": response.json()["message"],
        "retryable": retryable,
        "quota_charged": False,
    }


def _sqlite_busy_error() -> OperationalError:
    return OperationalError(
        "BEGIN IMMEDIATE",
        {},
        sqlite3.OperationalError("database is locked"),
    )


def test_analysis_claims_upload_and_charges_exactly_once(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    upload = _ready_upload(db_session, settings, upload_id="analysis-upload")

    response = client.post(
        "/api/tasks/analysis",
        headers=headers,
        json={"upload_id": upload.id},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "analysis"
    assert body["status"] == "queued"
    assert body["quota_charged"] is True
    assert body["style"] is None
    task = db_session.get(Task, body["task_id"])
    db_session.refresh(upload)
    assert task is not None
    assert task.upload_id == upload.id
    assert task.selected_hdu == 2
    assert task.input_path == upload.stored_path
    assert upload.claimed_at is not None
    assert upload.expires_at == task.expires_at
    usage = db_session.scalar(select(DailyUsage))
    assert usage is not None
    assert usage.count == 1


def test_sixth_task_is_rejected_at_daily_limit(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    for index in range(6):
        _ready_upload(db_session, settings, upload_id=f"quota-upload-{index}")

    for index in range(5):
        response = client.post(
            "/api/tasks/analysis",
            headers=headers,
            json={"upload_id": f"quota-upload-{index}"},
        )
        assert response.status_code == 201

    rejected = client.post(
        "/api/tasks/analysis",
        headers=headers,
        json={"upload_id": "quota-upload-5"},
    )

    _assert_error(rejected, 429, "daily_task_limit_reached")
    usage = db_session.scalar(select(DailyUsage))
    untouched = db_session.get(Upload, "quota-upload-5")
    assert usage is not None and usage.count == 5
    assert untouched is not None and untouched.claimed_at is None


@pytest.mark.parametrize(
    ("upload_id", "expires_at", "error_code"),
    [
        ("missing", None, "upload_not_found"),
        ("expired", datetime.now(UTC) - timedelta(seconds=1), "upload_expired"),
    ],
)
def test_failed_creation_does_not_increment_usage(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    upload_id: str,
    expires_at: datetime | None,
    error_code: str,
) -> None:
    if upload_id != "missing":
        _ready_upload(
            db_session,
            settings,
            upload_id=upload_id,
            expires_at=expires_at,
        )

    response = client.post(
        "/api/tasks/analysis",
        headers=headers,
        json={"upload_id": upload_id},
    )

    _assert_error(response, 404 if upload_id == "missing" else 410, error_code)
    assert db_session.scalar(select(func.count()).select_from(DailyUsage)) == 0
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0


def test_another_identity_cannot_claim_upload_by_id(
    client: TestClient,
    db_session: Session,
    settings: Settings,
) -> None:
    upload = _ready_upload(db_session, settings, upload_id="private-upload")

    response = client.post(
        "/api/tasks/analysis",
        headers={"X-Starun-Client-Id": "another-client"},
        json={"upload_id": upload.id},
    )

    _assert_error(response, 404, "upload_not_found")
    db_session.refresh(upload)
    assert upload.claimed_at is None
    assert db_session.scalar(select(func.count()).select_from(DailyUsage)) == 0


def test_same_upload_cannot_be_claimed_twice(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    upload = _ready_upload(db_session, settings, upload_id="single-claim")

    first = client.post(
        "/api/tasks/analysis",
        headers=headers,
        json={"upload_id": upload.id},
    )
    second = client.post(
        "/api/tasks/process",
        headers=headers,
        json={"upload_id": upload.id},
    )

    assert first.status_code == 201
    _assert_error(second, 409, "upload_already_claimed")
    usage = db_session.scalar(select(DailyUsage))
    assert usage is not None and usage.count == 1


def test_concurrent_creation_attempts_only_claim_upload_once(
    settings: Settings,
    db_session: Session,
) -> None:
    db_session.commit()
    engine, session_factory = create_engine_and_session(settings.database_url)
    with session_factory() as setup_session:
        _ready_upload(setup_session, settings, upload_id="collision")

    def create(task_type: TaskType) -> str:
        with session_factory() as session:
            service = TaskService(session, settings)
            try:
                if task_type is TaskType.ANALYSIS:
                    service.create_analysis("collision", "test-client", "testclient")
                else:
                    service.create_processing(
                        upload_id="collision",
                        source_task_id=None,
                        style=ProcessingStyle.BALANCED,
                        client_id="test-client",
                        request_ip="testclient",
                    )
            except TaskCreationError as exc:
                return exc.error_code
            return "created"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(create, [TaskType.ANALYSIS, TaskType.PROCESSING])
            )
        assert sorted(results) == ["created", "upload_already_claimed"]
        with session_factory() as session:
            usage = session.scalar(select(DailyUsage))
            assert usage is not None and usage.count == 1
            assert session.scalar(select(func.count()).select_from(Task)) == 1
    finally:
        engine.dispose()


def test_processing_defaults_balanced_and_validates_request_shape(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    upload = _ready_upload(db_session, settings, upload_id="process-upload")

    response = client.post(
        "/api/tasks/process",
        headers=headers,
        json={"upload_id": upload.id},
    )

    assert response.status_code == 201
    assert response.json()["style"] == "balanced"
    assert (
        db_session.get(Task, response.json()["task_id"]).style
        is ProcessingStyle.BALANCED
    )
    rejected = client.post(
        "/api/tasks/process",
        headers=headers,
        json={"upload_id": "one", "extra": True},
    )
    _assert_error(rejected, 422, "invalid_request")


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/tasks/process", {"upload_id": "one", "style": "documentary"}),
        ("/api/tasks/process", {}),
        (
            "/api/tasks/process",
            {"upload_id": "one", "source_task_id": "two"},
        ),
        ("/api/tasks/analysis", {}),
        ("/api/tasks/analysis", {"upload_id": 42}),
    ],
)
def test_task_validation_errors_have_stable_safe_format(
    client: TestClient,
    headers: dict[str, str],
    path: str,
    body: dict[str, Any],
) -> None:
    response = client.post(path, headers=headers, json=body)

    assert response.status_code == 422
    assert response.json() == {
        "error_code": "invalid_request",
        "message": "The request body is invalid.",
        "retryable": False,
        "quota_charged": False,
    }


@pytest.mark.parametrize(
    ("change", "status_code", "error_code"),
    [
        ({"status": TaskStatus.RUNNING}, 409, "source_task_invalid"),
        ({"type": TaskType.PROCESSING}, 409, "source_task_invalid"),
        ({"status": TaskStatus.EXPIRED}, 410, "source_file_expired"),
        ({"expires_at": datetime.now(UTC) - timedelta(seconds=1)}, 410, "source_file_expired"),
        ({"file_exists": False}, 410, "source_file_expired"),
        ({"client_id": "other-client"}, 409, "source_task_invalid"),
    ],
)
def test_source_reuse_requires_valid_owned_completed_analysis(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    change: dict[str, Any],
    status_code: int,
    error_code: str,
) -> None:
    source = _completed_analysis(
        db_session,
        settings,
        client_id=change.get("client_id", "test-client"),
        expires_at=change.get("expires_at"),
        file_exists=change.get("file_exists", True),
    )
    if "status" in change:
        source.status = change["status"]
    if "type" in change:
        source.type = change["type"]
    db_session.commit()

    response = client.post(
        "/api/tasks/process",
        headers=headers,
        json={"source_task_id": source.id},
    )

    _assert_error(response, status_code, error_code)
    assert db_session.scalar(select(func.count()).select_from(DailyUsage)) == 0


def test_source_reuse_charges_new_slot_without_mutating_source(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    source = _completed_analysis(db_session, settings)
    source_snapshot = {
        "status": source.status,
        "result_manifest": source.result_manifest,
        "expires_at": source.expires_at,
        "input_path": source.input_path,
    }

    response = client.post(
        "/api/tasks/process",
        headers=headers,
        json={"source_task_id": source.id, "style": "artistic"},
    )

    assert response.status_code == 201
    derived = db_session.get(Task, response.json()["task_id"])
    db_session.refresh(source)
    assert derived is not None
    assert derived.id != source.id
    assert derived.source_task_id == source.id
    assert derived.upload_id is None
    assert derived.input_path == source.input_path
    assert derived.selected_hdu == source.selected_hdu
    assert derived.expires_at == source.expires_at
    assert derived.style is ProcessingStyle.ARTISTIC
    assert {
        "status": source.status,
        "result_manifest": source.result_manifest,
        "expires_at": source.expires_at,
        "input_path": source.input_path,
    } == source_snapshot
    usage = db_session.scalar(select(DailyUsage))
    assert usage is not None and usage.count == 1


def test_get_usage_counts_analysis_and_processing(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    analysis_upload = _ready_upload(db_session, settings, upload_id="usage-analysis")
    processing_upload = _ready_upload(db_session, settings, upload_id="usage-processing")
    assert client.post(
        "/api/tasks/analysis",
        headers=headers,
        json={"upload_id": analysis_upload.id},
    ).status_code == 201
    assert client.post(
        "/api/tasks/process",
        headers=headers,
        json={"upload_id": processing_upload.id},
    ).status_code == 201

    response = client.get("/api/usage", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "date": datetime.now(UTC).date().isoformat(),
        "limit": settings.daily_task_limit,
        "used": 2,
        "remaining": settings.daily_task_limit - 2,
    }


def test_task_ids_are_opaque_and_have_at_least_128_bits_of_entropy(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    ids = []
    for index in range(5):
        upload = _ready_upload(db_session, settings, upload_id=f"entropy-{index}")
        response = client.post(
            "/api/tasks/analysis",
            headers=headers,
            json={"upload_id": upload.id},
        )
        assert response.status_code == 201
        ids.append(response.json()["task_id"])

    assert len(set(ids)) == 5
    assert all(len(task_id) >= 22 for task_id in ids)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", task_id) for task_id in ids)
    assert all(not task_id.startswith(("task-", "analysis-", "processing-")) for task_id in ids)


def test_exhausted_sqlite_contention_returns_retryable_store_busy(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = _ready_upload(db_session, settings, upload_id="busy-upload")
    original_execute = db_session.execute
    begin_attempts = 0

    def locked_execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal begin_attempts
        if str(statement).strip().upper() == "BEGIN IMMEDIATE":
            begin_attempts += 1
            raise _sqlite_busy_error()
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", locked_execute)

    response = client.post(
        "/api/tasks/analysis",
        headers=headers,
        json={"upload_id": upload.id},
    )

    _assert_error(response, 503, "task_store_busy", retryable=True)
    assert begin_attempts > 1
    monkeypatch.setattr(db_session, "execute", original_execute)
    db_session.expire_all()
    persisted_upload = db_session.get(Upload, upload.id)
    assert persisted_upload is not None and persisted_upload.claimed_at is None
    assert db_session.scalar(select(func.count()).select_from(DailyUsage)) == 0
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0


def test_transient_sqlite_contention_retries_without_double_charge(
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = _ready_upload(db_session, settings, upload_id="transient-busy")
    original_execute = db_session.execute
    begin_attempts = 0

    def transient_execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal begin_attempts
        if str(statement).strip().upper() == "BEGIN IMMEDIATE":
            begin_attempts += 1
            if begin_attempts == 1:
                raise _sqlite_busy_error()
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", transient_execute)

    task = TaskService(db_session, settings).create_analysis(
        upload.id,
        "test-client",
        "testclient",
    )

    assert task.status is TaskStatus.QUEUED
    assert begin_attempts == 2
    usage = db_session.scalar(select(DailyUsage))
    assert usage is not None and usage.count == 1
    assert db_session.scalar(select(func.count()).select_from(Task)) == 1


def test_unrelated_operational_error_is_not_retried(
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = _ready_upload(db_session, settings, upload_id="unrelated-db-error")
    original_execute = db_session.execute
    begin_attempts = 0

    def broken_execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal begin_attempts
        if str(statement).strip().upper() == "BEGIN IMMEDIATE":
            begin_attempts += 1
            raise OperationalError(
                "BEGIN IMMEDIATE",
                {},
                sqlite3.OperationalError("disk I/O error"),
            )
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", broken_execute)

    with pytest.raises(OperationalError):
        TaskService(db_session, settings).create_analysis(
            upload.id,
            "test-client",
            "testclient",
        )

    assert begin_attempts == 1


def test_distinct_uploads_racing_for_last_quota_slot_do_not_overcharge(
    settings: Settings,
    db_session: Session,
) -> None:
    db_session.commit()
    engine, session_factory = create_engine_and_session(settings.database_url)
    with session_factory() as setup_session:
        _ready_upload(setup_session, settings, upload_id="last-slot-a")
        _ready_upload(setup_session, settings, upload_id="last-slot-b")
        setup_session.add(
            DailyUsage(
                date=datetime.now(UTC).date(),
                client_id_hash=_hash("test-client"),
                ip_hash=_hash("testclient"),
                count=settings.daily_task_limit - 1,
            )
        )
        setup_session.commit()

    barrier = Barrier(2)

    def create(upload_id: str) -> str:
        with session_factory() as session:
            barrier.wait()
            try:
                TaskService(session, settings).create_analysis(
                    upload_id,
                    "test-client",
                    "testclient",
                )
            except TaskCreationError as exc:
                return exc.error_code
            return "created"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create, ["last-slot-a", "last-slot-b"]))
        assert sorted(results) == ["created", "daily_task_limit_reached"]
        with session_factory() as session:
            usage = session.scalar(select(DailyUsage))
            uploads = session.scalars(
                select(Upload).where(Upload.id.in_(["last-slot-a", "last-slot-b"]))
            ).all()
            assert usage is not None and usage.count == settings.daily_task_limit
            assert sum(upload.claimed_at is not None for upload in uploads) == 1
            assert session.scalar(select(func.count()).select_from(Task)) == 1
    finally:
        engine.dispose()


def test_task_commit_failure_rolls_back_usage_and_upload_claim(
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = _ready_upload(db_session, settings, upload_id="commit-failure")

    def fail_commit() -> None:
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(db_session, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="injected commit failure"):
        TaskService(db_session, settings).create_analysis(
            upload.id,
            "test-client",
            "testclient",
        )

    db_session.expire_all()
    persisted_upload = db_session.get(Upload, upload.id)
    assert persisted_upload is not None and persisted_upload.claimed_at is None
    assert db_session.scalar(select(func.count()).select_from(DailyUsage)) == 0
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0


def test_usage_is_charged_to_the_injected_clocks_utc_date(
    db_session: Session,
    settings: Settings,
) -> None:
    upload = _ready_upload(db_session, settings, upload_id="utc-boundary")
    local_time = datetime(
        2026,
        6,
        13,
        0,
        30,
        tzinfo=timezone(timedelta(hours=14)),
    )
    upload.expires_at = local_time + timedelta(hours=1)
    db_session.commit()

    TaskService(
        db_session,
        settings,
        clock=lambda: local_time,
    ).create_analysis(upload.id, "test-client", "testclient")

    usage = db_session.scalar(select(DailyUsage))
    assert usage is not None
    assert usage.date == date(2026, 6, 12)
    assert db_session.execute(
        text("SELECT count FROM daily_usage WHERE date = '2026-06-12'")
    ).scalar_one() == 1
