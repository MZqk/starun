import asyncio
import importlib
import shutil
import threading
import time
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.models import (
    EventLevel,
    ProcessingStyle,
    Task,
    TaskEvent,
    TaskStatus,
    TaskType,
    Upload,
    UploadStatus,
)
from app.uploads.errors import UploadError
from app.uploads.service import CHUNK_SIZE, UploadService
from app.tasks.service import TaskService
from app.usage.service import hash_identity

NOW = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)


def _cleanup_service(db_session: Session, settings: Settings) -> Any:
    try:
        module = importlib.import_module("app.cleanup.service")
    except ModuleNotFoundError:
        pytest.fail("Task 12 cleanup service is not implemented")
    return module.CleanupService(db_session, settings, clock=lambda: NOW)


def _terminal_task(
    db_session: Session,
    settings: Settings,
    task_id: str,
    *,
    expires_at: datetime,
) -> Task:
    source = settings.data_root / "uploads" / task_id / "input.fits"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    task_dir = settings.data_root / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "result.json").write_bytes(b"result")
    task = Task(
        id=task_id,
        type=TaskType.PROCESSING,
        status=TaskStatus.COMPLETED,
        stage="completed",
        progress=100,
        client_id_hash=hash_identity("test-client"),
        ip_hash=hash_identity("testclient"),
        style=ProcessingStyle.BALANCED,
        selected_hdu=2,
        input_path=str(source),
        result_manifest={"artifacts": [{"name": "result.json"}], "summary": {"ok": True}},
        quota_charged=True,
        created_at=NOW - timedelta(days=2),
        started_at=NOW - timedelta(days=2),
        finished_at=expires_at - timedelta(hours=24),
        expires_at=expires_at,
    )
    db_session.add(task)
    db_session.flush()
    db_session.add(
        TaskEvent(
            task_id=task.id,
            sequence=1,
            level=EventLevel.INFO,
            event_type="task_completed",
            payload={"progress": 100},
            created_at=task.finished_at,
        )
    )
    db_session.commit()
    return task


def _claimed_upload_for_task(
    db_session: Session,
    task: Task,
    *,
    upload_id: str,
) -> Upload:
    upload = Upload(
        id=upload_id,
        client_id_hash=task.client_id_hash,
        ip_hash=task.ip_hash,
        original_file_name="input.fits",
        stored_path=task.input_path or "",
        size_bytes=6,
        status=UploadStatus.READY,
        validation_result={"valid": True},
        selected_hdu=task.selected_hdu,
        created_at=NOW - timedelta(days=2),
        expires_at=task.expires_at or NOW,
        claimed_at=task.created_at,
    )
    db_session.add(upload)
    db_session.flush()
    task.upload_id = upload.id
    db_session.commit()
    return upload


def _upload(
    db_session: Session,
    settings: Settings,
    upload_id: str,
    *,
    status: UploadStatus,
    claimed_at: datetime | None = None,
) -> Upload:
    stored_path = settings.data_root / "uploads" / upload_id / "input.fits"
    stored_path.parent.mkdir(parents=True)
    stored_path.write_bytes(b"upload")
    upload = Upload(
        id=upload_id,
        client_id_hash=hash_identity("test-client"),
        ip_hash=hash_identity("testclient"),
        original_file_name="input.fits",
        stored_path=str(stored_path),
        size_bytes=6,
        status=status,
        validation_result=None,
        selected_hdu=None,
        created_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
        claimed_at=claimed_at,
    )
    db_session.add(upload)
    db_session.commit()
    return upload


def test_cleanup_expires_task_removes_files_and_keeps_minimal_row(
    db_session: Session,
    settings: Settings,
) -> None:
    task = _terminal_task(db_session, settings, "expired-task", expires_at=NOW)
    task_dir = settings.data_root / "tasks" / task.id
    source = Path(task.input_path or "")

    _cleanup_service(db_session, settings).run_once()
    db_session.refresh(task)

    assert task.status is TaskStatus.EXPIRED
    assert task.error_code == "task_expired"
    assert task.stage is None
    assert task.progress == 0
    assert task.result_manifest is None
    assert task.input_path is None
    assert task.upload_id is None
    assert task.source_task_id is None
    assert task.style is None
    assert task.selected_hdu is None
    assert task.cleanup_pending is False
    assert task.cleanup_error is None
    assert task.cleanup_plan is None
    assert not task_dir.exists()
    assert not source.exists()
    assert db_session.scalars(
        select(TaskEvent).where(TaskEvent.task_id == task.id)
    ).all() == []


def test_scheduled_task_cleanup_commits_intent_before_deleting_files(
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _terminal_task(db_session, settings, "task-intent", expires_at=NOW)
    task_dir = settings.data_root / "tasks" / task.id
    source = Path(task.input_path or "")
    original_commit = db_session.commit
    failed = False

    def fail_intent_commit_once() -> None:
        nonlocal failed
        if not failed and task.cleanup_pending:
            failed = True
            raise OperationalError(
                "intent busy",
                {},
                __import__("sqlite3").OperationalError("database is locked"),
            )
        original_commit()

    monkeypatch.setattr(db_session, "commit", fail_intent_commit_once)

    with pytest.raises(OperationalError):
        _cleanup_service(db_session, settings).run_once()

    assert task_dir.is_dir()
    assert source.is_file()
    db_session.rollback()
    db_session.expire_all()
    persisted = db_session.get(Task, task.id)
    assert persisted is not None
    assert persisted.status is TaskStatus.COMPLETED
    assert persisted.cleanup_pending is False


def test_scheduled_expiry_deletes_claimed_upload_row_with_owned_source(
    db_session: Session,
    settings: Settings,
) -> None:
    task = _terminal_task(db_session, settings, "claimed-expiry", expires_at=NOW)
    upload = _claimed_upload_for_task(db_session, task, upload_id="claimed-expiry-upload")
    service = _cleanup_service(db_session, settings)

    service.run_once()
    service.run_once()
    db_session.refresh(task)

    assert task.status is TaskStatus.EXPIRED
    assert task.upload_id is None
    assert db_session.get(Upload, upload.id) is None
    assert not Path(upload.stored_path).exists()


def test_user_delete_deletes_claimed_upload_row_with_owned_source(
    db_session: Session,
    settings: Settings,
) -> None:
    task = _terminal_task(db_session, settings, "claimed-delete", expires_at=NOW)
    upload = _claimed_upload_for_task(db_session, task, upload_id="claimed-delete-upload")
    task.delete_requested_at = NOW
    task.cleanup_pending = True
    task.error_code = "user_deleted"
    db_session.commit()
    service = _cleanup_service(db_session, settings)

    service.run_once()
    service.run_once()
    db_session.refresh(task)

    assert task.status is TaskStatus.EXPIRED
    assert task.error_code == "user_deleted"
    assert task.upload_id is None
    assert db_session.get(Upload, upload.id) is None
    assert not Path(upload.stored_path).exists()


def test_cleanup_waits_until_exact_terminal_expiry(
    db_session: Session,
    settings: Settings,
) -> None:
    task = _terminal_task(
        db_session,
        settings,
        "not-expired",
        expires_at=NOW + timedelta(microseconds=1),
    )

    _cleanup_service(db_session, settings).run_once()
    db_session.refresh(task)

    assert task.status is TaskStatus.COMPLETED
    assert (settings.data_root / "tasks" / task.id).is_dir()


@pytest.mark.parametrize("status", [UploadStatus.INVALID, UploadStatus.READY])
def test_cleanup_deletes_stale_invalid_or_unclaimed_upload(
    db_session: Session,
    settings: Settings,
    status: UploadStatus,
) -> None:
    upload = _upload(db_session, settings, f"stale-{status.value}", status=status)
    upload_dir = Path(upload.stored_path).parent

    _cleanup_service(db_session, settings).run_once()

    assert db_session.get(Upload, upload.id) is None
    assert not upload_dir.exists()


@pytest.mark.parametrize("status", [UploadStatus.UPLOADING, UploadStatus.VALIDATING])
def test_cleanup_never_deletes_active_upload_past_ttl(
    db_session: Session,
    settings: Settings,
    status: UploadStatus,
) -> None:
    upload = _upload(db_session, settings, f"active-{status.value}", status=status)

    _cleanup_service(db_session, settings).run_once()

    assert db_session.get(Upload, upload.id) is not None
    assert Path(upload.stored_path).is_file()


def test_startup_marks_interrupted_active_upload_invalid(
    db_session: Session,
    settings: Settings,
) -> None:
    upload = _upload(
        db_session,
        settings,
        "interrupted-upload",
        status=UploadStatus.VALIDATING,
    )
    module = importlib.import_module("app.cleanup.service")
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)

    assert module.recover_interrupted_uploads(factory) == 1
    db_session.expire_all()

    recovered = db_session.get(Upload, upload.id)
    assert recovered is not None
    assert recovered.status is UploadStatus.INVALID
    assert recovered.validation_result == {"error_code": "upload_interrupted"}
    assert recovered.cleanup_pending is True
    assert recovered.cleanup_plan == {
        "version": 1,
        "upload_dir": ["uploads", recovered.id],
    }


@pytest.mark.parametrize("symlink_component", ["uploads", "data-root"])
def test_upload_cleanup_rejects_symlinked_parent_without_deleting_outside(
    db_session: Session,
    settings: Settings,
    tmp_path: Path,
    symlink_component: str,
) -> None:
    upload = _upload(db_session, settings, "symlink-upload", status=UploadStatus.INVALID)
    outside = tmp_path / f"outside-{symlink_component}"
    outside_upload = outside / "uploads" / upload.id
    outside_upload.mkdir(parents=True)
    outside_file = outside_upload / "input.fits"
    outside_file.write_bytes(b"outside")
    if symlink_component == "uploads":
        shutil.rmtree(settings.data_root / "uploads")
        (settings.data_root / "uploads").symlink_to(
            outside / "uploads",
            target_is_directory=True,
        )
    else:
        moved = tmp_path / "moved-data-root"
        settings.data_root.rename(moved)
        settings.data_root.symlink_to(outside, target_is_directory=True)

    _cleanup_service(db_session, settings).run_once()

    assert outside_file.read_bytes() == b"outside"
    persisted = db_session.get(Upload, upload.id)
    assert persisted is not None and persisted.cleanup_pending is True


def test_upload_cleanup_commits_intent_before_filesystem_delete(
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = _upload(db_session, settings, "intent-upload", status=UploadStatus.READY)
    original_commit = db_session.commit
    failed = False

    def fail_intent_commit_once() -> None:
        nonlocal failed
        if not failed and upload.cleanup_pending:
            failed = True
            raise OperationalError(
                "intent busy",
                {},
                __import__("sqlite3").OperationalError("database is locked"),
            )
        original_commit()

    monkeypatch.setattr(db_session, "commit", fail_intent_commit_once)

    with pytest.raises(OperationalError):
        _cleanup_service(db_session, settings).run_once()

    assert Path(upload.stored_path).is_file()
    db_session.rollback()
    db_session.expire_all()
    persisted = db_session.get(Upload, upload.id)
    assert persisted is not None
    assert persisted.status is UploadStatus.READY
    assert persisted.cleanup_pending is False


def test_upload_cleanup_filesystem_failure_retries_from_durable_intent(
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = _upload(db_session, settings, "retry-upload", status=UploadStatus.READY)
    service = _cleanup_service(db_session, settings)
    original = service._remove_upload_directory
    attempts = 0

    def fail_once(row: Upload) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("controlled cleanup failure")
        original(row)

    monkeypatch.setattr(service, "_remove_upload_directory", fail_once)

    service.run_once()
    persisted = db_session.get(Upload, upload.id)
    assert persisted is not None
    assert persisted.status is UploadStatus.INVALID
    assert persisted.cleanup_pending is True
    assert Path(persisted.stored_path).is_file()

    service.run_once()

    assert db_session.get(Upload, upload.id) is None
    assert not Path(upload.stored_path).exists()


@pytest.mark.asyncio
async def test_direct_upload_failure_persists_cleanup_intent_for_scheduler_retry(
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = UploadFile(BytesIO(b"invalid"), filename="input.fits")

    def fail_cleanup(
        _self: UploadService,
        _upload_dir: Path | None,
        _upload_id: str,
    ) -> dict[str, object]:
        return {
            "cleanup_pending": True,
            "cleanup_diagnostic_id": "controlled",
        }

    monkeypatch.setattr(UploadService, "_remove_upload_dir", fail_cleanup)

    with pytest.raises(UploadError):
        await UploadService(db_session, settings).create(
            file,
            "test-client",
            "testclient",
        )

    row = db_session.scalar(select(Upload))
    assert row is not None
    assert row.status is UploadStatus.INVALID
    assert row.cleanup_pending is True
    assert row.cleanup_plan == {
        "version": 1,
        "upload_dir": ["uploads", row.id],
    }

    _cleanup_service(db_session, settings).run_once()

    assert db_session.get(Upload, row.id) is None


def test_upload_cleanup_phase_b_commit_failure_retries_missing_directory(
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = _upload(db_session, settings, "phase-b-upload", status=UploadStatus.READY)
    source = Path(upload.stored_path)
    original_commit = db_session.commit
    failed = False

    def fail_phase_b_once() -> None:
        nonlocal failed
        if not failed and not source.exists():
            failed = True
            raise OperationalError(
                "phase b busy",
                {},
                __import__("sqlite3").OperationalError("database is locked"),
            )
        original_commit()

    monkeypatch.setattr(db_session, "commit", fail_phase_b_once)
    service = _cleanup_service(db_session, settings)

    with pytest.raises(OperationalError):
        service.run_once()
    db_session.rollback()
    db_session.expire_all()
    pending = db_session.get(Upload, upload.id)
    assert pending is not None
    assert pending.status is UploadStatus.INVALID
    assert pending.cleanup_pending is True
    assert not source.exists()

    service.run_once()

    assert db_session.get(Upload, upload.id) is None


def test_cleanup_keeps_claimed_upload(
    db_session: Session,
    settings: Settings,
) -> None:
    upload = _upload(
        db_session,
        settings,
        "claimed",
        status=UploadStatus.READY,
        claimed_at=NOW - timedelta(hours=1),
    )

    _cleanup_service(db_session, settings).run_once()

    assert db_session.get(Upload, upload.id) is not None
    assert Path(upload.stored_path).is_file()


def test_cleanup_is_idempotent_and_tolerates_missing_directories(
    db_session: Session,
    settings: Settings,
) -> None:
    task = _terminal_task(db_session, settings, "missing-task-dir", expires_at=NOW)
    upload = _upload(db_session, settings, "missing-upload-dir", status=UploadStatus.INVALID)
    shutil.rmtree(settings.data_root / "tasks" / task.id)
    shutil.rmtree(Path(upload.stored_path).parent)
    service = _cleanup_service(db_session, settings)

    service.run_once()
    service.run_once()

    persisted = db_session.get(Task, task.id)
    assert persisted is not None and persisted.status is TaskStatus.EXPIRED
    assert persisted.error_code == "task_expired"
    assert db_session.get(Upload, upload.id) is None


@pytest.mark.parametrize("missing_task_directory", [False, True])
def test_cleanup_converges_pending_user_deletion_without_overwriting_reason(
    db_session: Session,
    settings: Settings,
    missing_task_directory: bool,
) -> None:
    task = _terminal_task(db_session, settings, "pending-delete", expires_at=NOW)
    task.delete_requested_at = NOW - timedelta(minutes=5)
    task.cleanup_pending = True
    task.error_code = "user_deleted"
    task.error_message = None
    db_session.commit()
    task_dir = settings.data_root / "tasks" / task.id
    if missing_task_directory:
        shutil.rmtree(task_dir)
    service = _cleanup_service(db_session, settings)

    service.run_once()
    service.run_once()
    db_session.refresh(task)

    assert task.status is TaskStatus.EXPIRED
    assert task.error_code == "user_deleted"
    assert task.delete_requested_at is not None
    assert task.cleanup_pending is False
    assert task.cleanup_error is None
    assert task.cleanup_plan is None
    assert not task_dir.exists()


def test_interrupted_scheduled_expiry_retry_preserves_task_expired_reason(
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _terminal_task(db_session, settings, "expiry-reason", expires_at=NOW)
    service = _cleanup_service(db_session, settings)
    original_remove = TaskService._perform_filesystem_cleanup
    attempts = 0

    def fail_once(task_service: TaskService, plan: dict[str, object]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("controlled expiry cleanup failure")
        original_remove(task_service, plan)

    monkeypatch.setattr(TaskService, "_perform_filesystem_cleanup", fail_once)

    service.run_once()
    db_session.expire_all()
    pending = db_session.get(Task, task.id)
    assert pending is not None
    assert pending.status is TaskStatus.EXPIRED
    assert pending.error_code == "task_expired"
    assert pending.delete_requested_at is None
    assert pending.cleanup_pending is True
    assert pending.cleanup_plan is not None
    assert pending.cleanup_plan["reason"] == "task_expired"

    service.run_once()
    db_session.expire_all()
    expired = db_session.get(Task, task.id)
    assert expired is not None
    assert expired.error_code == "task_expired"
    assert expired.cleanup_pending is False


def test_user_delete_cleanup_plan_persists_distinct_reason(
    db_session: Session,
    settings: Settings,
) -> None:
    task = _terminal_task(db_session, settings, "delete-reason", expires_at=NOW)
    task.delete_requested_at = NOW
    task.cleanup_pending = True
    task.error_code = "user_deleted"
    task.cleanup_plan = TaskService(
        db_session,
        settings,
        clock=lambda: NOW,
    )._cleanup_plan(task, NOW, reason="user_deleted")
    db_session.commit()

    _cleanup_service(db_session, settings).run_once()
    db_session.expire_all()

    deleted = db_session.get(Task, task.id)
    assert deleted is not None
    assert deleted.error_code == "user_deleted"
    assert deleted.cleanup_pending is False


@pytest.mark.parametrize(
    "reference_status",
    [TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLING],
)
def test_cleanup_plan_preserves_shared_input_for_active_reference_with_past_expiry(
    db_session: Session,
    settings: Settings,
    reference_status: TaskStatus,
) -> None:
    owner = _terminal_task(db_session, settings, "shared-owner", expires_at=NOW)
    reference = Task(
        id=f"active-{reference_status.value}",
        type=TaskType.PROCESSING,
        status=reference_status,
        stage=reference_status.value,
        progress=10,
        client_id_hash=owner.client_id_hash,
        ip_hash=owner.ip_hash,
        source_task_id=owner.id,
        input_path=owner.input_path,
        quota_charged=True,
        created_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
    )
    db_session.add(reference)
    db_session.commit()

    plan = TaskService(db_session, settings, clock=lambda: NOW)._cleanup_plan(owner, NOW)

    assert plan["source_file"] is None


def test_shared_active_reference_preserves_claimed_upload_until_final_reference_cleanup(
    db_session: Session,
    settings: Settings,
) -> None:
    owner = _terminal_task(db_session, settings, "retained-owner", expires_at=NOW)
    upload = _claimed_upload_for_task(db_session, owner, upload_id="retained-upload")
    source = Path(upload.stored_path)
    reference = Task(
        id="retained-reference",
        type=TaskType.PROCESSING,
        status=TaskStatus.RUNNING,
        stage="running",
        progress=20,
        client_id_hash=owner.client_id_hash,
        ip_hash=owner.ip_hash,
        source_task_id=owner.id,
        input_path=owner.input_path,
        quota_charged=True,
        created_at=NOW - timedelta(hours=1),
        expires_at=NOW - timedelta(minutes=1),
    )
    db_session.add(reference)
    db_session.commit()
    service = _cleanup_service(db_session, settings)

    service.run_once()
    db_session.refresh(owner)

    assert owner.status is TaskStatus.EXPIRED
    assert owner.upload_id is None
    assert db_session.get(Upload, upload.id) is not None
    assert source.is_file()

    reference.status = TaskStatus.COMPLETED
    reference.finished_at = NOW
    reference.expires_at = NOW
    db_session.commit()
    service.run_once()
    service.run_once()
    db_session.refresh(reference)

    assert reference.status is TaskStatus.EXPIRED
    assert reference.upload_id is None
    assert db_session.get(Upload, upload.id) is None
    assert not source.exists()


def test_pending_delete_revalidates_persisted_plan_before_deleting_shared_input(
    db_session: Session,
    settings: Settings,
) -> None:
    owner = _terminal_task(db_session, settings, "planned-owner", expires_at=NOW)
    service = TaskService(db_session, settings, clock=lambda: NOW)
    owner.delete_requested_at = NOW
    owner.cleanup_pending = True
    owner.error_code = "user_deleted"
    owner.cleanup_plan = service._cleanup_plan(owner, NOW)
    source = Path(owner.input_path or "")
    db_session.commit()
    reference = Task(
        id="late-active-reference",
        type=TaskType.PROCESSING,
        status=TaskStatus.RUNNING,
        stage="running",
        progress=20,
        client_id_hash=owner.client_id_hash,
        ip_hash=owner.ip_hash,
        source_task_id=owner.id,
        input_path=owner.input_path,
        quota_charged=True,
        created_at=NOW,
        expires_at=NOW - timedelta(hours=1),
    )
    db_session.add(reference)
    db_session.commit()

    service.finalize_delete(owner.id)

    assert source.is_file()
    deleted = db_session.get(Task, owner.id)
    assert deleted is not None and deleted.error_code == "user_deleted"


@pytest.mark.parametrize(
    ("reference_status", "reference_expires_at", "protects_source"),
    [
        (TaskStatus.COMPLETED, NOW + timedelta(seconds=1), True),
        (TaskStatus.FAILED, NOW, False),
        (TaskStatus.CANCELLED, NOW - timedelta(seconds=1), False),
        (TaskStatus.EXPIRED, NOW + timedelta(hours=1), False),
    ],
)
def test_cleanup_plan_terminal_reference_protection_follows_lifecycle(
    db_session: Session,
    settings: Settings,
    reference_status: TaskStatus,
    reference_expires_at: datetime,
    protects_source: bool,
) -> None:
    owner = _terminal_task(db_session, settings, "terminal-owner", expires_at=NOW)
    reference = Task(
        id=f"terminal-{reference_status.value}",
        type=TaskType.PROCESSING,
        status=reference_status,
        stage=reference_status.value,
        progress=100,
        client_id_hash=owner.client_id_hash,
        ip_hash=owner.ip_hash,
        source_task_id=owner.id,
        input_path=owner.input_path,
        quota_charged=True,
        created_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(hours=1),
        expires_at=reference_expires_at,
    )
    db_session.add(reference)
    db_session.commit()

    plan = TaskService(db_session, settings, clock=lambda: NOW)._cleanup_plan(owner, NOW)

    assert (plan["source_file"] is None) is protects_source


@pytest.mark.asyncio
async def test_upload_disk_guard_rechecks_after_each_chunk_and_cleans_partial_file(
    db_session: Session,
    settings: Settings,
) -> None:
    settings.max_upload_bytes = 3 * CHUNK_SIZE
    settings.min_free_disk_bytes = 100
    checks = 0

    def disk_usage(_path: Path) -> Any:
        nonlocal checks
        checks += 1
        partial_files = list((settings.data_root / "uploads").glob("*/input.fits"))
        written = partial_files[0].stat().st_size if partial_files else 0
        free = 50 if written == 2 * CHUNK_SIZE else 4 * CHUNK_SIZE
        return shutil._ntuple_diskusage(4 * CHUNK_SIZE, 0, free)

    file = UploadFile(
        BytesIO(b"a" * CHUNK_SIZE + b"b" * CHUNK_SIZE),
        filename="input.fits",
    )

    with pytest.raises(UploadError) as exc_info:
        await UploadService(
            db_session,
            settings,
            disk_usage=disk_usage,
        ).create(file, "test-client", "testclient")

    assert exc_info.value.status_code == 507
    row = db_session.scalar(select(Upload))
    assert row is not None and row.status is UploadStatus.INVALID
    assert not Path(row.stored_path).exists()
    assert not Path(row.stored_path).parent.exists()
    assert checks >= 3


@pytest.mark.asyncio
async def test_cleanup_scheduler_runs_off_loop_without_overlap_and_stops_cleanly(
    settings: Settings,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("app.cleanup.service")
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    active = 0
    max_active = 0
    lock = threading.Lock()

    def blocking_run_once(_service: Any) -> None:
        nonlocal calls, active, max_active
        with lock:
            calls += 1
            active += 1
            max_active = max(max_active, active)
        entered.set()
        release.wait(timeout=1)
        with lock:
            active -= 1

    monkeypatch.setattr(module.CleanupService, "run_once", blocking_run_once)
    session_factory = __import__(
        "sqlalchemy.orm",
        fromlist=["sessionmaker"],
    ).sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    scheduler = module.CleanupScheduler(
        session_factory,
        settings,
        interval_seconds=0.001,
    )

    scheduler.start()
    scheduler.start()
    assert await asyncio.to_thread(entered.wait, 0.2)
    heartbeat_started = time.monotonic()
    await asyncio.sleep(0.01)
    assert time.monotonic() - heartbeat_started < 0.1
    release.set()
    await asyncio.sleep(0.02)
    await scheduler.stop()

    assert calls >= 1
    assert max_active == 1


@pytest.mark.asyncio
async def test_cleanup_scheduler_stop_start_waits_for_same_worker_to_finish(
    settings: Settings,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("app.cleanup.service")
    entered = threading.Event()
    release = threading.Event()
    active = 0
    max_active = 0
    calls = 0
    lock = threading.Lock()

    def blocking_run_once(_service: Any) -> None:
        nonlocal active, max_active, calls
        with lock:
            active += 1
            calls += 1
            max_active = max(max_active, active)
        entered.set()
        release.wait(timeout=1)
        with lock:
            active -= 1

    monkeypatch.setattr(module.CleanupService, "run_once", blocking_run_once)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    scheduler = module.CleanupScheduler(factory, settings, interval_seconds=10)

    scheduler.start()
    assert await asyncio.to_thread(entered.wait, 0.2)
    stopping = asyncio.create_task(scheduler.stop())
    await asyncio.sleep(0.01)
    assert not stopping.done()
    scheduler.start()
    await asyncio.sleep(0.01)
    assert calls == 1
    release.set()
    await stopping

    entered.clear()
    release.clear()
    scheduler.start()
    assert await asyncio.to_thread(entered.wait, 0.2)
    release.set()
    await scheduler.stop()

    assert calls == 2
    assert max_active == 1
