import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.artifacts.store import ArtifactStore
from app.config import Settings
from app.db.models import (
    DailyUsage,
    EventLevel,
    ProcessingStyle,
    Task,
    TaskEvent,
    TaskStatus,
    TaskType,
    Upload,
    UploadStatus,
)
import numpy as np
from astropy.io import fits
from app.db.session import create_engine_and_session
from app.tasks.events import TaskEventService
from app.usage.service import hash_identity


def _task(
    session: Session,
    settings: Settings,
    task_id: str,
    *,
    status: TaskStatus = TaskStatus.QUEUED,
    task_type: TaskType = TaskType.ANALYSIS,
    error_code: str | None = None,
    quota_charged: bool = True,
    source_task_id: str | None = None,
    input_path: Path | None = None,
    expires_at: datetime | None = None,
) -> Task:
    now = datetime.now(UTC)
    source = input_path or settings.data_root / "uploads" / task_id / "input.fits"
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        primary_hdu = fits.PrimaryHDU()
        image_hdu1 = fits.ImageHDU(data=np.zeros((32, 48), dtype=np.float32), name="IMAGE_1")
        image_hdu2 = fits.ImageHDU(data=np.zeros((32, 48), dtype=np.float32), name="IMAGE_2")
        fits.HDUList([primary_hdu, image_hdu1, image_hdu2]).writeto(source, overwrite=True)

    selected = {
        "index": 2,
        "name": "IMAGE_2",
        "kind": "image",
        "shape": [32, 48],
        "dtype": "float32",
        "supported": True,
    }
    validation_result = {
        "hdus": [
            {
                "index": 0,
                "name": "PRIMARY",
                "kind": "primary_header",
                "shape": [],
                "dtype": "",
                "supported": False,
            },
            {
                "index": 1,
                "name": "IMAGE_1",
                "kind": "image",
                "shape": [32, 48],
                "dtype": "float32",
                "supported": True,
            },
            selected,
        ],
        "selected_hdu": selected,
        "statistics": {
            "minimum": 0.0,
            "maximum": 1.0,
            "mean": 0.4,
            "median": 0.35,
            "standard_deviation": 0.2,
            "finite_pixel_count": 1536,
        },
        "header": {"OBJECT": "M42", "EXPTIME": 120.0},
    }

    upload = Upload(
        id=f"{task_id}-upload",
        client_id_hash=hash_identity("test-client"),
        ip_hash=hash_identity("testclient"),
        original_file_name="input.fits",
        stored_path=str(source),
        size_bytes=source.stat().st_size,
        status=UploadStatus.READY,
        validation_result=validation_result,
        selected_hdu=2,
        created_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(days=1),
        claimed_at=now - timedelta(minutes=5),
    )
    session.add(upload)

    task = Task(
        id=task_id,
        type=task_type,
        status=status,
        stage=status.value,
        progress=100 if status is TaskStatus.COMPLETED else 20,
        client_id_hash=hash_identity("test-client"),
        ip_hash=hash_identity("testclient"),
        source_task_id=source_task_id,
        style=ProcessingStyle.BALANCED if task_type is TaskType.PROCESSING else None,
        selected_hdu=2,
        upload=upload,
        input_path=str(source),
        error_code=error_code,
        error_message="internal detail" if error_code else None,
        retryable=error_code is not None,
        quota_charged=quota_charged,
        created_at=now - timedelta(minutes=5),
        started_at=now - timedelta(minutes=4) if status is not TaskStatus.QUEUED else None,
        finished_at=now - timedelta(minutes=1)
        if status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        else None,
        expires_at=expires_at or now + timedelta(hours=1),
    )
    session.add(task)
    session.commit()
    return task


def _artifact_task(
    session: Session,
    settings: Settings,
    task_id: str = "artifact-task",
) -> tuple[Task, bytes]:
    task = _task(session, settings, task_id, status=TaskStatus.COMPLETED)
    data = b'{"safe":true}\n'
    with ArtifactStore(settings.data_root / "tasks" / task.id) as store:
        entry = store.write_bytes("result.json", data)
    task.result_manifest = {
        "artifacts": [entry.model_dump(mode="json")],
        "summary": {"safe": True},
        "inspection": {"selected_hdu": {"index": 2}},
    }
    session.commit()
    return task, data


def _assert_error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert body["error_code"] == code
    assert body["retryable"] is False
    assert body["quota_charged"] is False


def test_get_task_is_owned_typed_and_does_not_expose_paths(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    task, _ = _artifact_task(db_session, settings)

    response = client.get(f"/api/tasks/{task.id}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == task.id
    assert body["status"] == "completed"
    assert body["result"]["manifest_available"] is True
    assert body["selected_hdu"] == 2
    serialized = response.text.lower()
    assert "input_path" not in serialized
    assert str(settings.data_root).lower() not in serialized
    _assert_error(
        client.get(
            f"/api/tasks/{task.id}",
            headers={"X-Starun-Client-Id": "foreign"},
        ),
        404,
        "task_not_found",
    )


def test_events_are_incremental_ordered_bounded_and_validate_cursor(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    task = _task(db_session, settings, "event-task")
    factory = TaskEventService(
        cast(Any, getattr(cast(FastAPI, client.app).state.task_executor, "session_factory"))
    )
    for sequence in range(205):
        factory.append(
            task.id,
            EventLevel.INFO,
            "progress",
            {"value": sequence, "path": None},
        )

    first = client.get(f"/api/tasks/{task.id}/events?after=0", headers=headers)
    second = client.get(f"/api/tasks/{task.id}/events?after=200", headers=headers)

    assert first.status_code == 200
    assert [event["sequence"] for event in first.json()["events"]] == list(range(1, 201))
    assert first.json()["next_after"] == 200
    assert first.json()["has_more"] is True
    assert [event["sequence"] for event in second.json()["events"]] == list(range(201, 206))
    _assert_error(
        client.get(f"/api/tasks/{task.id}/events?after=-1", headers=headers),
        422,
        "invalid_event_cursor",
    )
    _assert_error(
        client.get(f"/api/tasks/{task.id}/events?after=invalid", headers=headers),
        422,
        "invalid_event_cursor",
    )


def test_event_and_result_serialization_redacts_cross_platform_paths(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    task, _ = _artifact_task(db_session, settings, "safe-serialization")
    internal_path = task.input_path
    assert internal_path is not None
    task.result_manifest["summary"] = {
        "safe": "visible",
        "posix": "/private/input.fits",
        "relative": "relative/private/input.fits",
        "windows": r"C:\private\input.fits",
        "unc": r"\\server\share\input.fits",
        "uri": "file:///private/input.fits",
        "traversal": "../private/input.fits",
        "internal": internal_path,
        "nested": {"generic": r"D:\secret\result.tiff", "count": 2},
    }
    task.events.append(
        TaskEvent(
            sequence=1,
            level=EventLevel.INFO,
            event_type="custom",
            payload={
                "safe": "visible",
                "relative": "relative/private/input.fits",
                "windows": r"C:\private\input.fits",
                "unc": r"\\server\share\input.fits",
                "uri": "file:///private/input.fits",
                "traversal": "../private/input.fits",
                "internal": internal_path,
                "nested": {"generic": "/private/result.tiff", "count": 2},
            },
        )
    )
    db_session.commit()

    detail = client.get(f"/api/tasks/{task.id}", headers=headers)
    events = client.get(f"/api/tasks/{task.id}/events", headers=headers)

    summary = detail.json()["result"]["summary"]
    assert summary == {
        "safe": "visible",
        "posix": "[redacted]",
        "relative": "[redacted]",
        "windows": "[redacted]",
        "unc": "[redacted]",
        "uri": "[redacted]",
        "traversal": "[redacted]",
        "internal": "[redacted]",
        "nested": {"generic": "[redacted]", "count": 2},
    }
    assert detail.json()["result"]["artifacts"] == ["result.json"]
    assert events.json()["events"][0]["payload"] == {
        "safe": "visible",
        "relative": "[redacted]",
        "windows": "[redacted]",
        "unc": "[redacted]",
        "uri": "[redacted]",
        "traversal": "[redacted]",
        "internal": "[redacted]",
        "nested": {"generic": "[redacted]", "count": 2},
    }


def test_agent_guardrail_has_public_message(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    task = _task(
        db_session,
        settings,
        "guardrail-message",
        status=TaskStatus.FAILED,
        error_code="agent_guardrail",
    )

    response = client.get(f"/api/tasks/{task.id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["message"] == "Agent output was rejected."


@pytest.mark.parametrize(
    ("status", "expected_status", "http_status"),
    [
        (TaskStatus.QUEUED, TaskStatus.CANCELLING, 200),
        (TaskStatus.RUNNING, TaskStatus.CANCELLING, 200),
        (TaskStatus.COMPLETED, TaskStatus.COMPLETED, 200),
    ],
)
def test_cancel_is_atomic_idempotent_and_notifies(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    status: TaskStatus,
    expected_status: TaskStatus,
    http_status: int,
) -> None:
    task = _task(db_session, settings, f"cancel-{status.value}", status=status)
    notifications = 0

    class RecordingExecutor:
        def notify(self) -> None:
            nonlocal notifications
            notifications += 1

    cast(FastAPI, client.app).state.task_executor = RecordingExecutor()

    response = client.post(f"/api/tasks/{task.id}/cancel", headers=headers)
    repeated = client.post(f"/api/tasks/{task.id}/cancel", headers=headers)

    assert response.status_code == http_status
    assert repeated.status_code == http_status
    db_session.expire_all()
    persisted = db_session.get(Task, task.id)
    assert persisted is not None and persisted.status is expected_status
    if status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
        assert persisted.cancel_requested_at is not None
        assert notifications == 2
    else:
        assert notifications == 0


def test_first_eligible_retry_is_free_then_charged_and_unique(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    source = _task(
        db_session,
        settings,
        "retry-source",
        status=TaskStatus.FAILED,
        error_code="restart_interrupted",
    )

    first = client.post(f"/api/tasks/{source.id}/retry", headers=headers)
    second = client.post(f"/api/tasks/{source.id}/retry", headers=headers)

    assert first.status_code == 201
    assert first.json()["quota_charged"] is False
    assert second.status_code == 201
    assert second.json()["quota_charged"] is True
    assert first.json()["task_id"] != second.json()["task_id"]
    db_session.expire_all()
    persisted = db_session.get(Task, source.id)
    usage = db_session.scalar(select(DailyUsage))
    assert persisted is not None and persisted.free_retry_used is True
    assert usage is not None and usage.count == 1


def test_ineligible_retry_charges_and_obeys_daily_limit(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    source = _task(db_session, settings, "completed-retry", status=TaskStatus.COMPLETED)
    db_session.add(
        DailyUsage(
            date=datetime.now(UTC).date(),
            client_id_hash=source.client_id_hash,
            ip_hash=source.ip_hash,
            count=settings.daily_task_limit,
        )
    )
    db_session.commit()

    response = client.post(f"/api/tasks/{source.id}/retry", headers=headers)

    _assert_error(response, 429, "daily_task_limit_reached")


def test_concurrent_retry_allows_exactly_one_free_retry(
    settings: Settings,
    db_session: Session,
) -> None:
    source = _task(
        db_session,
        settings,
        "concurrent-retry",
        status=TaskStatus.FAILED,
        error_code="task_timeout",
    )
    db_session.commit()
    engine, factory = create_engine_and_session(settings.database_url)

    def retry() -> tuple[str, bool]:
        from app.tasks.service import TaskService

        with factory() as session:
            task = TaskService(session, settings).retry(
                source.id,
                "test-client",
                "testclient",
            )
            return task.id, task.quota_charged

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: retry(), range(2)))
        assert len({task_id for task_id, _ in results}) == 2
        assert sorted(charged for _, charged in results) == [False, True]
    finally:
        engine.dispose()


@pytest.mark.parametrize("missing", [True, False])
def test_retry_rejects_missing_or_expired_source_file(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    missing: bool,
) -> None:
    task = _task(
        db_session,
        settings,
        f"retry-file-{missing}",
        status=TaskStatus.FAILED,
        error_code="system_error",
        expires_at=datetime.now(UTC) - timedelta(seconds=1) if not missing else None,
    )
    if missing:
        Path(task.input_path or "").unlink()

    response = client.post(f"/api/tasks/{task.id}/retry", headers=headers)

    _assert_error(response, 410, "source_file_expired")


def test_retry_rejects_task_with_delete_intent_without_mutation(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    task = _task(
        db_session,
        settings,
        "retry-deleting",
        status=TaskStatus.RUNNING,
        error_code="task_timeout",
    )
    original_source = task.input_path

    deleted = client.delete(f"/api/tasks/{task.id}", headers=headers)
    retried = client.post(f"/api/tasks/{task.id}/retry", headers=headers)

    assert deleted.status_code == 202
    _assert_error(retried, 409, "task_deleting")
    db_session.expire_all()
    persisted = db_session.get(Task, task.id)
    assert persisted is not None
    assert persisted.delete_requested_at is not None
    assert persisted.cleanup_pending is True
    assert persisted.free_retry_used is False
    assert persisted.input_path == original_source
    assert db_session.scalar(select(DailyUsage)) is None
    assert db_session.scalars(select(Task).where(Task.id != task.id)).all() == []


def test_retry_rejects_deleted_tombstone_as_expired(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    task = _task(db_session, settings, "retry-deleted", status=TaskStatus.COMPLETED)

    assert client.delete(f"/api/tasks/{task.id}", headers=headers).status_code == 200
    retried = client.post(f"/api/tasks/{task.id}/retry", headers=headers)

    _assert_error(retried, 410, "task_expired")
    assert db_session.scalar(select(DailyUsage)) is None


def test_processing_cannot_reference_source_pending_deletion(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _task(
        db_session,
        settings,
        "source-pending-delete",
        status=TaskStatus.COMPLETED,
    )
    task_dir = settings.data_root / "tasks" / source.id
    task_dir.mkdir(parents=True)
    (task_dir / "result.json").write_bytes(b"result")
    monkeypatch.setattr(
        "app.tasks.service.shutil.rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cleanup failed")),
    )

    assert client.delete(f"/api/tasks/{source.id}", headers=headers).status_code == 200
    response = client.post(
        "/api/tasks/process",
        headers=headers,
        json={"source_task_id": source.id, "style": "balanced"},
    )

    _assert_error(response, 409, "source_task_invalid")
    db_session.expire_all()
    persisted = db_session.get(Task, source.id)
    assert persisted is not None and persisted.cleanup_pending is True
    assert db_session.scalars(select(Task).where(Task.id != source.id)).all() == []


def test_concurrent_delete_and_retry_never_retries_after_delete_intent(
    settings: Settings,
    db_session: Session,
) -> None:
    task = _task(
        db_session,
        settings,
        "delete-retry-race",
        status=TaskStatus.FAILED,
        error_code="task_timeout",
    )
    engine, factory = create_engine_and_session(settings.database_url)
    barrier = __import__("threading").Barrier(2)

    def delete() -> str:
        from app.tasks.service import TaskService

        barrier.wait()
        with factory() as session:
            deleted, _pending = TaskService(session, settings).delete(
                task.id,
                "test-client",
                "testclient",
            )
            return deleted.status.value

    def retry() -> tuple[str, str]:
        from app.tasks.service import TaskCreationError, TaskService

        barrier.wait()
        with factory() as session:
            try:
                retried = TaskService(session, settings).retry(
                    task.id,
                    "test-client",
                    "testclient",
                )
                return "created", retried.id
            except TaskCreationError as exc:
                return "rejected", exc.error_code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            delete_future = pool.submit(delete)
            retry_future = pool.submit(retry)
            delete_future.result()
            retry_result = retry_future.result()
        with factory() as session:
            source = session.get(Task, task.id)
            assert source is not None and source.delete_requested_at is not None
            created = list(session.scalars(select(Task).where(Task.id != task.id)))
            if retry_result[0] == "rejected":
                assert retry_result == ("rejected", "task_deleting")
                assert created == []
                assert source.free_retry_used is False
            else:
                assert len(created) == 1
                assert created[0].id == retry_result[1]
                assert created[0].created_at <= source.delete_requested_at
    finally:
        engine.dispose()


def test_delete_completed_respects_shared_source_and_is_idempotent(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    shared = settings.data_root / "uploads" / "shared" / "input.fits"
    source = _task(
        db_session,
        settings,
        "delete-source",
        status=TaskStatus.COMPLETED,
        input_path=shared,
    )
    derived = _task(
        db_session,
        settings,
        "delete-derived",
        status=TaskStatus.COMPLETED,
        task_type=TaskType.PROCESSING,
        source_task_id=source.id,
        input_path=shared,
    )
    task_dir = settings.data_root / "tasks" / source.id
    task_dir.mkdir(parents=True)
    (task_dir / "result.json").write_bytes(b"result")

    first = client.delete(f"/api/tasks/{source.id}", headers=headers)
    second = client.delete(f"/api/tasks/{source.id}", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert shared.exists()
    assert not task_dir.exists()
    db_session.expire_all()
    deleted = db_session.get(Task, source.id)
    live = db_session.get(Task, derived.id)
    assert deleted is not None and deleted.status is TaskStatus.EXPIRED
    assert deleted.error_code == "user_deleted"
    assert deleted.input_path is None and deleted.result_manifest is None
    assert deleted.cleanup_pending is False
    assert deleted.cleanup_error is None
    assert live is not None and live.input_path == str(shared)

    last = client.delete(f"/api/tasks/{derived.id}", headers=headers)

    assert last.status_code == 200
    assert not shared.exists()


def test_successful_delete_removes_events_and_leaves_minimal_audit(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    task, _ = _artifact_task(db_session, settings, "delete-audit")
    task.error_message = "private diagnostic"
    task.events.append(
        TaskEvent(
            sequence=1,
            level=EventLevel.INFO,
            event_type="completed",
            payload={"detail": "private"},
        )
    )
    db_session.commit()

    response = client.delete(f"/api/tasks/{task.id}", headers=headers)
    events = client.get(f"/api/tasks/{task.id}/events", headers=headers)

    assert response.status_code == 200
    assert response.json()["result"] == {
        "manifest_available": False,
        "summary": None,
        "artifacts": [],
    }
    assert response.json()["inspection"] is None
    assert response.json()["selected_hdu"] is None
    assert events.status_code == 200
    assert events.json()["events"] == []
    db_session.expire_all()
    persisted = db_session.get(Task, task.id)
    assert persisted is not None and persisted.status is TaskStatus.EXPIRED
    assert persisted.stage is None
    assert persisted.progress == 0
    assert persisted.error_code == "user_deleted"
    assert persisted.error_message is None
    assert persisted.retryable is False
    assert persisted.result_manifest is None
    assert persisted.input_path is None
    assert persisted.upload_id is None
    assert persisted.source_task_id is None
    assert persisted.style is None
    assert persisted.selected_hdu is None
    assert db_session.scalars(
        select(TaskEvent).where(TaskEvent.task_id == task.id)
    ).all() == []


def test_cleanup_failure_retains_references_and_repeated_delete_retries(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, _ = _artifact_task(db_session, settings, "delete-retry")
    original_input_path = task.input_path
    original_manifest = task.result_manifest
    task_dir = settings.data_root / "tasks" / task.id
    real_rmtree = __import__("shutil").rmtree
    attempts = 0

    def fail_once(path: Path | str, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError(f"cannot remove {path}")
        real_rmtree(path, **kwargs)

    monkeypatch.setattr("app.tasks.service.shutil.rmtree", fail_once)

    first = client.delete(f"/api/tasks/{task.id}", headers=headers)

    assert first.status_code == 200
    db_session.expire_all()
    pending = db_session.get(Task, task.id)
    assert pending is not None
    assert pending.cleanup_pending is True
    assert pending.cleanup_error is not None
    assert str(settings.data_root) not in pending.cleanup_error
    assert pending.cleanup_plan
    assert pending.input_path == original_input_path
    assert pending.result_manifest == original_manifest
    assert task_dir.exists()

    second = client.delete(f"/api/tasks/{task.id}", headers=headers)

    assert second.status_code == 200
    db_session.expire_all()
    deleted = db_session.get(Task, task.id)
    assert deleted is not None
    assert deleted.status is TaskStatus.EXPIRED
    assert deleted.cleanup_pending is False
    assert deleted.cleanup_error is None
    assert deleted.cleanup_plan is None
    assert deleted.input_path is None
    assert deleted.result_manifest is None
    assert not task_dir.exists()


def test_cleanup_plan_contains_only_validated_relative_components(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    task = _task(
        db_session,
        settings,
        "relative-cleanup-plan",
        status=TaskStatus.RUNNING,
    )

    response = client.delete(f"/api/tasks/{task.id}", headers=headers)

    assert response.status_code == 202
    db_session.expire_all()
    pending = db_session.get(Task, task.id)
    assert pending is not None
    assert pending.cleanup_plan == {
        "version": 1,
        "reason": "user_deleted",
        "task_dir": ["tasks", task.id],
        "source_file": ["uploads", task.id, "input.fits"],
    }
    serialized = str(pending.cleanup_plan)
    assert str(settings.data_root) not in serialized
    assert ".." not in serialized


def test_cleanup_rejects_symlinked_parent_without_touching_outside(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-tasks"
    outside.mkdir()
    outside_task = outside / "symlink-parent"
    outside_task.mkdir()
    outside_artifact = outside_task / "result.json"
    outside_artifact.write_bytes(b"outside")
    (settings.data_root / "tasks").symlink_to(outside, target_is_directory=True)
    task = _task(
        db_session,
        settings,
        "symlink-parent",
        status=TaskStatus.COMPLETED,
    )

    response = client.delete(f"/api/tasks/{task.id}", headers=headers)

    assert response.status_code == 200
    assert outside_artifact.read_bytes() == b"outside"
    db_session.expire_all()
    pending = db_session.get(Task, task.id)
    assert pending is not None
    assert pending.cleanup_pending is True
    assert pending.cleanup_error is not None
    assert pending.input_path is not None
    assert pending.cleanup_plan is not None


def test_phase_b_commit_failure_keeps_durable_plan_and_retry_finalizes(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, _ = _artifact_task(db_session, settings, "phase-b-retry")
    original_input = task.input_path
    original_manifest = task.result_manifest
    task_dir = settings.data_root / "tasks" / task.id
    source = Path(original_input or "")
    original_commit = db_session.commit
    failed_once = False

    def fail_phase_b_commit_once() -> None:
        nonlocal failed_once
        if not failed_once and not task_dir.exists() and not source.exists():
            failed_once = True
            raise OperationalError(
                "phase b busy",
                {},
                __import__("sqlite3").OperationalError("database is locked"),
            )
        original_commit()

    monkeypatch.setattr(db_session, "commit", fail_phase_b_commit_once)

    first = client.delete(f"/api/tasks/{task.id}", headers=headers)

    assert first.status_code == 503
    assert first.json()["error_code"] == "task_store_busy"
    assert first.json()["retryable"] is True
    assert not task_dir.exists()
    assert not source.exists()
    db_session.expire_all()
    pending = db_session.get(Task, task.id)
    assert pending is not None
    assert pending.cleanup_pending is True
    assert pending.cleanup_plan is not None
    assert pending.input_path == original_input
    assert pending.result_manifest == original_manifest

    second = client.delete(f"/api/tasks/{task.id}", headers=headers)

    assert second.status_code == 200
    db_session.expire_all()
    deleted = db_session.get(Task, task.id)
    assert deleted is not None
    assert deleted.status is TaskStatus.EXPIRED
    assert deleted.cleanup_pending is False
    assert deleted.cleanup_plan is None
    assert deleted.input_path is None
    assert deleted.result_manifest is None


def test_permanent_phase_b_db_failure_is_503_and_keeps_cleanup_retryable(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, _ = _artifact_task(db_session, settings, "phase-b-unavailable")
    original_input = task.input_path
    original_manifest = task.result_manifest
    task_dir = settings.data_root / "tasks" / task.id
    source = Path(original_input or "")
    original_commit = db_session.commit
    failed_once = False

    def fail_phase_b_commit_once() -> None:
        nonlocal failed_once
        if not failed_once and not task_dir.exists() and not source.exists():
            failed_once = True
            raise IntegrityError(
                "phase b unavailable",
                {},
                __import__("sqlite3").IntegrityError("constraint failure"),
            )
        original_commit()

    monkeypatch.setattr(db_session, "commit", fail_phase_b_commit_once)

    response = client.delete(f"/api/tasks/{task.id}", headers=headers)

    assert response.status_code == 503
    assert response.json() == {
        "error_code": "task_store_unavailable",
        "message": "The task store is temporarily unavailable.",
        "retryable": True,
        "quota_charged": False,
    }
    assert not task_dir.exists()
    assert not source.exists()
    db_session.expire_all()
    pending = db_session.get(Task, task.id)
    assert pending is not None
    assert pending.status is TaskStatus.EXPIRED
    assert pending.cleanup_pending is True
    assert pending.cleanup_plan is not None
    assert pending.input_path == original_input
    assert pending.result_manifest == original_manifest


def test_delete_openapi_documents_cleanup_store_503_errors(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/tasks/{task_id}"]["delete"]
    response = operation["responses"]["503"]

    assert response["content"]["application/json"]["schema"]["$ref"].endswith(
        "/TaskErrorResponse"
    )
    examples = response["content"]["application/json"]["examples"]
    assert examples["busy"]["value"]["error_code"] == "task_store_busy"
    assert examples["unavailable"]["value"]["error_code"] == "task_store_unavailable"
    assert examples["busy"]["value"]["retryable"] is True
    assert examples["unavailable"]["value"]["retryable"] is True


def test_delete_rejects_escaped_server_owned_paths(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.fits"
    outside.write_bytes(b"outside")
    task = _task(
        db_session,
        settings,
        "delete-escape",
        status=TaskStatus.COMPLETED,
        input_path=outside,
    )

    response = client.delete(f"/api/tasks/{task.id}", headers=headers)

    assert response.status_code == 200
    assert outside.exists()
    db_session.expire_all()
    deleted = db_session.get(Task, task.id)
    assert deleted is not None and deleted.status is TaskStatus.EXPIRED


def test_delete_queued_endpoint_is_finalized_by_executor(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    task = _task(
        db_session,
        settings,
        "delete-queued-e2e",
        status=TaskStatus.QUEUED,
        task_type=TaskType.PROCESSING,
    )
    task_dir = settings.data_root / "tasks" / task.id
    task_dir.mkdir(parents=True)
    (task_dir / "partial.tiff").write_bytes(b"partial")

    response = client.delete(f"/api/tasks/{task.id}", headers=headers)

    assert response.status_code == 202
    assert response.json()["cleanup_pending"] is True
    executor = cast(FastAPI, client.app).state.task_executor
    asyncio.run(executor.run_until_idle())
    db_session.expire_all()

    status = client.get(f"/api/tasks/{task.id}", headers=headers)
    events = client.get(f"/api/tasks/{task.id}/events", headers=headers)
    assert status.status_code == 200
    assert status.json()["status"] == "expired"
    assert status.json()["error_code"] == "user_deleted"
    assert status.json()["cleanup_pending"] is False
    assert events.json()["events"] == []
    assert not task_dir.exists()


def test_artifact_download_validates_manifest_hash_and_headers(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    task, data = _artifact_task(db_session, settings, "download")

    response = client.get(
        f"/api/tasks/{task.id}/artifacts/result.json",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "application/json"
    assert response.headers["content-length"] == str(len(data))
    assert response.headers["content-disposition"] == 'attachment; filename="result.json"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"

    (settings.data_root / "tasks" / task.id / "result.json").write_bytes(b"mutated")
    _assert_error(
        client.get(f"/api/tasks/{task.id}/artifacts/result.json", headers=headers),
        410,
        "artifact_unavailable",
    )


@pytest.mark.parametrize("name", ["other.json", "..%2Finput.fits"])
def test_artifact_rejects_nonmanifest_and_traversal(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    name: str,
) -> None:
    task, _ = _artifact_task(db_session, settings, f"reject-{name[:3]}")

    response = client.get(f"/api/tasks/{task.id}/artifacts/{name}", headers=headers)

    assert response.status_code in {404, 410}
    assert response.json()["error_code"] in {"artifact_not_found", "artifact_unavailable"}


def test_artifact_rejects_symlink_and_special_file(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    tmp_path: Path,
) -> None:
    for task_id, make_bad in [
        ("symlink", lambda path: path.symlink_to(tmp_path / "outside.json")),
        ("fifo", lambda path: os.mkfifo(path)),
    ]:
        task, _ = _artifact_task(db_session, settings, task_id)
        artifact = settings.data_root / "tasks" / task.id / "result.json"
        artifact.unlink()
        (tmp_path / "outside.json").write_bytes(b"outside")
        make_bad(artifact)

        response = client.get(
            f"/api/tasks/{task.id}/artifacts/result.json",
            headers=headers,
        )

        _assert_error(response, 410, "artifact_unavailable")


def test_foreign_identity_cannot_mutate_or_download(
    client: TestClient,
    db_session: Session,
    settings: Settings,
) -> None:
    task, _ = _artifact_task(db_session, settings, "private-task")
    foreign = {"X-Starun-Client-Id": "foreign"}

    responses = [
        client.get(f"/api/tasks/{task.id}", headers=foreign),
        client.get(f"/api/tasks/{task.id}/events", headers=foreign),
        client.post(f"/api/tasks/{task.id}/cancel", headers=foreign),
        client.post(f"/api/tasks/{task.id}/retry", headers=foreign),
        client.delete(f"/api/tasks/{task.id}", headers=foreign),
        client.get(f"/api/tasks/{task.id}/artifacts/result.json", headers=foreign),
    ]

    assert all(response.status_code == 404 for response in responses)
    assert all(response.json()["error_code"] == "task_not_found" for response in responses)


def test_no_task_list_route(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get("/api/tasks", headers=headers)

    assert response.status_code == 404
