import asyncio
import json
import shutil
import sqlite3
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread
from typing import Any, cast
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.session import get_db_session
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
from app.db.session import create_engine_and_session
from app.main import build_lifespan, create_app
from app.agent.runner import AgentCancelledError
from app.tasks import events as task_events
from app.tasks.events import TaskEventService
from app.tasks.executor import HandlerResult, SerialTaskExecutor, TaskHandlerError
from app.tasks.handlers import AnalysisTaskHandler, ProcessingTaskHandler
from app.tasks.recovery import recover_interrupted_tasks


FIXED_NOW = datetime(2026, 6, 12, 8, 30, tzinfo=UTC)


@pytest.fixture
def session_factory(
    settings: Settings,
) -> Generator[sessionmaker[Session], None, None]:
    engine, factory = create_engine_and_session(settings.database_url)
    Base.metadata.create_all(engine)
    yield factory
    engine.dispose()


def _inspection() -> dict[str, Any]:
    selected = {
        "index": 0,
        "name": "PRIMARY",
        "kind": "primary_image",
        "shape": [32, 48],
        "dtype": "float32",
        "supported": True,
    }
    return {
        "hdus": [selected],
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


def _task(
    factory: sessionmaker[Session],
    settings: Settings,
    task_id: str,
    task_type: TaskType,
    *,
    status: TaskStatus = TaskStatus.QUEUED,
    created_at: datetime = FIXED_NOW,
    style: ProcessingStyle | None = None,
) -> Task:
    source = settings.data_root / "uploads" / task_id / "input.fits"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"SIMPLE DETERMINISTIC FITS")
    with factory() as session:
        upload = Upload(
            id=f"{task_id}-upload",
            client_id_hash="client",
            ip_hash="ip",
            original_file_name="input.fits",
            stored_path=str(source),
            size_bytes=source.stat().st_size,
            status=UploadStatus.READY,
            validation_result=_inspection(),
            selected_hdu=0,
            created_at=created_at,
            expires_at=created_at + timedelta(days=1),
            claimed_at=created_at,
        )
        task = Task(
            id=task_id,
            type=task_type,
            status=status,
            client_id_hash="client",
            ip_hash="ip",
            upload=upload,
            style=style,
            selected_hdu=0,
            input_path=str(source),
            created_at=created_at,
            expires_at=created_at + timedelta(days=1),
        )
        session.add(task)
        session.commit()
        return task


def _events(factory: sessionmaker[Session], task_id: str) -> list[TaskEvent]:
    with factory() as session:
        return list(
            session.scalars(
                select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.sequence)
            )
        )


async def _wait_for_status(
    factory: sessionmaker[Session],
    task_id: str,
    expected: TaskStatus,
    *,
    timeout: float = 0.5,
) -> None:
    async def wait() -> None:
        while True:
            with factory() as session:
                task = session.get(Task, task_id)
                if task is not None and task.status is expected:
                    return
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait(), timeout=timeout)


def _transient_db_error(message: str) -> OperationalError:
    return OperationalError(
        message,
        {},
        sqlite3.OperationalError(message),
    )


def _permanent_db_error(message: str) -> IntegrityError:
    return IntegrityError(
        message,
        {},
        sqlite3.IntegrityError(message),
    )


def test_event_sequence_is_atomic_and_unique_under_concurrent_appends(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    _task(session_factory, settings, "events", TaskType.ANALYSIS)
    barrier = Barrier(8)

    def append(index: int) -> int:
        barrier.wait()
        return (
            TaskEventService(session_factory, clock=lambda: FIXED_NOW)
            .append(
                "events",
                EventLevel.INFO,
                "concurrent",
                {"index": index},
            )
            .sequence
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        sequences = list(pool.map(append, range(8)))

    assert sorted(sequences) == list(range(1, 9))
    assert [event.sequence for event in _events(session_factory, "events")] == list(range(1, 9))
    assert all(event.created_at == FIXED_NOW for event in _events(session_factory, "events"))


def test_executor_and_event_service_reject_non_sqlite_engines(
    settings: Settings,
) -> None:
    engine = Mock()
    engine.dialect.name = "postgresql"
    factory = sessionmaker(bind=cast(Engine, engine))

    with pytest.raises(
        task_events.UnsupportedDatabaseError,
        match="SerialTaskExecutor requires SQLite",
    ):
        SerialTaskExecutor(factory, settings)
    with pytest.raises(
        task_events.UnsupportedDatabaseError,
        match="TaskEventService requires SQLite",
    ):
        TaskEventService(factory)


@pytest.mark.asyncio
async def test_executor_runs_fifo_with_global_concurrency_one(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    _task(session_factory, settings, "second", TaskType.ANALYSIS, created_at=FIXED_NOW)
    _task(
        session_factory,
        settings,
        "first",
        TaskType.ANALYSIS,
        created_at=FIXED_NOW - timedelta(seconds=1),
    )
    order: list[str] = []
    active = 0
    maximum = 0

    async def handler(task_id: str) -> HandlerResult:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        order.append(task_id)
        await asyncio.sleep(0.01)
        active -= 1
        return HandlerResult(result_manifest={"task_id": task_id})

    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.ANALYSIS: handler},
        clock=lambda: FIXED_NOW,
    )

    await executor.run_until_idle()

    assert order == ["first", "second"]
    assert maximum == 1
    with session_factory() as session:
        tasks = {task.id: task for task in session.scalars(select(Task))}
        assert all(task.status is TaskStatus.COMPLETED for task in tasks.values())
        assert all(task.progress == 100 for task in tasks.values())
        assert all(task.finished_at == FIXED_NOW for task in tasks.values())
        assert all(task.expires_at == FIXED_NOW + timedelta(hours=24) for task in tasks.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_status", [TaskStatus.QUEUED, TaskStatus.RUNNING])
async def test_executor_finalizes_active_delete_without_terminal_event(
    session_factory: sessionmaker[Session],
    settings: Settings,
    initial_status: TaskStatus,
) -> None:
    task = _task(
        session_factory,
        settings,
        f"active-delete-{initial_status.value}",
        TaskType.PROCESSING,
        status=initial_status,
        style=ProcessingStyle.BALANCED,
    )
    task_dir = settings.data_root / "tasks" / task.id
    task_dir.mkdir(parents=True)
    (task_dir / "partial.tiff").write_bytes(b"partial")
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()
    handler_calls = 0

    async def handler(_task_id: str) -> HandlerResult:
        nonlocal handler_calls
        handler_calls += 1
        handler_started.set()
        await release_handler.wait()
        return HandlerResult(result_manifest={"summary": {"private": True}})

    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.PROCESSING: handler},
        clock=lambda: FIXED_NOW,
    )

    if initial_status is TaskStatus.RUNNING:
        processing = asyncio.create_task(executor._process(task.id))
        await asyncio.wait_for(handler_started.wait(), timeout=0.2)

    engine, api_factory = create_engine_and_session(settings.database_url)
    try:
        with api_factory() as session:
            persisted = session.get(Task, task.id)
            assert persisted is not None
            persisted.delete_requested_at = FIXED_NOW
            persisted.cancel_requested_at = FIXED_NOW
            persisted.status = TaskStatus.CANCELLING
            persisted.cleanup_pending = True
            session.commit()
    finally:
        engine.dispose()

    if initial_status is TaskStatus.RUNNING:
        release_handler.set()
        await processing
    else:
        await executor.run_until_idle()

    with session_factory() as session:
        deleted = session.get(Task, task.id)
        assert deleted is not None
        assert deleted.status is TaskStatus.EXPIRED
        assert deleted.error_code == "user_deleted"
        assert deleted.cleanup_pending is False
        assert deleted.input_path is None
        assert deleted.result_manifest is None
        assert deleted.stage is None
    assert handler_calls == (1 if initial_status is TaskStatus.RUNNING else 0)
    assert not task_dir.exists()
    assert _events(session_factory, task.id) == []


@pytest.mark.asyncio
async def test_notify_from_another_thread_wakes_worker_promptly(
    session_factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drained = asyncio.Event()
    started = asyncio.Event()
    thread_errors: list[BaseException] = []
    thread_safe_callbacks: list[Callable[..., object]] = []

    async def handler(_task_id: str) -> HandlerResult:
        started.set()
        return HandlerResult(result_manifest={})

    class WaitingExecutor(SerialTaskExecutor):
        async def run_until_idle(self) -> None:
            await super().run_until_idle()
            drained.set()

    executor = WaitingExecutor(
        session_factory,
        settings,
        handlers={TaskType.ANALYSIS: handler},
        poll_interval_seconds=60,
    )
    loop = asyncio.get_running_loop()
    original_call_soon_threadsafe = loop.call_soon_threadsafe

    def record_call_soon_threadsafe(
        callback: Callable[..., object],
        *args: object,
    ) -> asyncio.Handle:
        thread_safe_callbacks.append(callback)
        return original_call_soon_threadsafe(callback, *args)

    monkeypatch.setattr(loop, "call_soon_threadsafe", record_call_soon_threadsafe)
    executor.start()
    try:
        await asyncio.wait_for(drained.wait(), timeout=0.2)
        await asyncio.sleep(0)
        _task(session_factory, settings, "thread-notify", TaskType.ANALYSIS)

        def notify() -> None:
            try:
                executor.notify()
            except BaseException as exc:
                thread_errors.append(exc)

        thread = Thread(target=notify)
        thread.start()
        thread.join()

        await asyncio.wait_for(started.wait(), timeout=0.2)
        assert thread_errors == []
        assert thread_safe_callbacks
    finally:
        await executor.stop()


@pytest.mark.asyncio
async def test_notify_before_start_is_delivered_after_worker_begins(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    second_drain = asyncio.Event()

    class PendingNotifyExecutor(SerialTaskExecutor):
        drain_count = 0

        async def run_until_idle(self) -> None:
            self.drain_count += 1
            if self.drain_count == 2:
                second_drain.set()

    executor = PendingNotifyExecutor(
        session_factory,
        settings,
        handlers={},
        poll_interval_seconds=60,
    )
    thread = Thread(target=executor.notify)
    thread.start()
    thread.join()

    executor.start()
    try:
        await asyncio.wait_for(second_drain.wait(), timeout=0.2)
    finally:
        await executor.stop()


@pytest.mark.asyncio
async def test_worker_does_not_lose_notify_at_drain_boundary(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    started = asyncio.Event()

    async def handler(_task_id: str) -> HandlerResult:
        started.set()
        return HandlerResult(result_manifest={})

    class BoundaryExecutor(SerialTaskExecutor):
        first_drain = True

        async def run_until_idle(self) -> None:
            if self.first_drain:
                self.first_drain = False
                _task(session_factory, settings, "boundary", TaskType.ANALYSIS)
                self.notify()
                return
            await super().run_until_idle()

    executor = BoundaryExecutor(
        session_factory,
        settings,
        handlers={TaskType.ANALYSIS: handler},
        poll_interval_seconds=60,
    )
    executor.start()
    try:
        await asyncio.wait_for(started.wait(), timeout=0.2)
    finally:
        await executor.stop()


@pytest.mark.asyncio
async def test_worker_survives_transient_lease_error_and_runs_next_task(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    _task(session_factory, settings, "lease-retry", TaskType.ANALYSIS)
    invoked: list[str] = []

    async def handler(task_id: str) -> HandlerResult:
        invoked.append(task_id)
        return HandlerResult(result_manifest={})

    class LeaseFailureExecutor(SerialTaskExecutor):
        failed_once = False

        def _lease_next(self) -> str | None:
            if not self.failed_once:
                self.failed_once = True
                raise _transient_db_error("lease unavailable")
            return super()._lease_next()

    executor = LeaseFailureExecutor(
        session_factory,
        settings,
        handlers={TaskType.ANALYSIS: handler},
        poll_interval_seconds=60,
        infrastructure_backoff_initial_seconds=0.001,
        infrastructure_backoff_max_seconds=0.01,
    )
    executor.start()
    try:
        executor.notify()
        await _wait_for_status(session_factory, "lease-retry", TaskStatus.COMPLETED)
        assert executor.is_running
        assert invoked == ["lease-retry"]
    finally:
        await executor.stop()


@pytest.mark.asyncio
async def test_worker_survives_transient_started_event_error(
    session_factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _task(session_factory, settings, "event-retry", TaskType.ANALYSIS)
    invoked: list[str] = []

    async def handler(task_id: str) -> HandlerResult:
        invoked.append(task_id)
        return HandlerResult(result_manifest={})

    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.ANALYSIS: handler},
        poll_interval_seconds=60,
        infrastructure_backoff_initial_seconds=0.001,
        infrastructure_backoff_max_seconds=0.01,
    )
    original_append = executor._events.append
    attempts = 0

    def fail_once(*args: object, **kwargs: object) -> TaskEvent:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _transient_db_error("database is locked")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(executor._events, "append", fail_once)
    executor.start()
    try:
        executor.notify()
        await _wait_for_status(session_factory, "event-retry", TaskStatus.COMPLETED)
        assert executor.is_running
        assert invoked == ["event-retry"]
        assert attempts >= 2
    finally:
        await executor.stop()


@pytest.mark.asyncio
async def test_terminal_event_failure_rolls_back_state_and_retries_without_reexecution(
    session_factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _task(
        session_factory,
        settings,
        "terminal-retry",
        TaskType.ANALYSIS,
        created_at=FIXED_NOW - timedelta(seconds=1),
    )
    _task(session_factory, settings, "terminal-next", TaskType.ANALYSIS)
    handler_calls: list[str] = []
    insert_failed = asyncio.Event()

    async def handler(task_id: str) -> HandlerResult:
        handler_calls.append(task_id)
        return HandlerResult(result_manifest={"done": True})

    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.ANALYSIS: handler},
        poll_interval_seconds=60,
        infrastructure_backoff_initial_seconds=0.2,
        infrastructure_backoff_max_seconds=0.2,
    )
    original_append_in_session = executor._events.append_in_session
    failed_once = False

    def fail_once(*args: object, **kwargs: object) -> TaskEvent:
        nonlocal failed_once
        event_type = cast(str, args[3])
        if event_type == "task_completed" and not failed_once:
            failed_once = True
            insert_failed.set()
            raise _transient_db_error("database is locked")
        return original_append_in_session(*args, **kwargs)

    monkeypatch.setattr(executor._events, "append_in_session", fail_once)
    executor.start()
    try:
        executor.notify()
        await asyncio.wait_for(insert_failed.wait(), timeout=0.2)
        with session_factory() as session:
            task = session.get(Task, "terminal-retry")
            assert task is not None
            assert task.status is TaskStatus.RUNNING
        assert "task_completed" not in {
            event.event_type for event in _events(session_factory, "terminal-retry")
        }

        await _wait_for_status(session_factory, "terminal-retry", TaskStatus.COMPLETED)
        await _wait_for_status(session_factory, "terminal-next", TaskStatus.COMPLETED)
        assert handler_calls == ["terminal-retry", "terminal-next"]
        assert [event.event_type for event in _events(session_factory, "terminal-retry")].count(
            "task_completed"
        ) == 1
    finally:
        await executor.stop()


@pytest.mark.asyncio
async def test_permanent_terminal_error_is_attempted_once_and_worker_stays_supervised(
    session_factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _task(session_factory, settings, "terminal-permanent", TaskType.ANALYSIS)
    handler_calls = 0
    terminal_attempts = 0
    supervised = asyncio.Event()

    async def handler(_task_id: str) -> HandlerResult:
        nonlocal handler_calls
        handler_calls += 1
        return HandlerResult(result_manifest={"done": True})

    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.ANALYSIS: handler},
        poll_interval_seconds=60,
        infrastructure_backoff_initial_seconds=0.001,
        infrastructure_backoff_max_seconds=0.01,
    )
    original_finish = executor._finish_completed

    def fail_permanently(task_id: str, result: HandlerResult) -> None:
        nonlocal terminal_attempts
        terminal_attempts += 1
        raise _permanent_db_error("invalid terminal event")

    monkeypatch.setattr(executor, "_finish_completed", fail_permanently)
    monkeypatch.setattr(
        "app.tasks.executor.logger.exception",
        lambda message, *_args, **_kwargs: (
            supervised.set()
            if message == "Serial task worker iteration failed"
            else None
        ),
    )
    executor.start()
    try:
        executor.notify()
        await asyncio.wait_for(supervised.wait(), timeout=0.2)
        await asyncio.sleep(0.03)
        assert executor.is_running
        assert handler_calls == 1
        assert terminal_attempts == 1
        with session_factory() as session:
            task = session.get(Task, "terminal-permanent")
            assert task is not None
            assert task.status is TaskStatus.RUNNING
    finally:
        monkeypatch.setattr(executor, "_finish_completed", original_finish)
        await executor.stop()


@pytest.mark.asyncio
async def test_executor_does_not_lease_when_another_task_is_running(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    _task(
        session_factory,
        settings,
        "already-running",
        TaskType.ANALYSIS,
        status=TaskStatus.RUNNING,
    )
    _task(session_factory, settings, "still-queued", TaskType.ANALYSIS)
    invoked: list[str] = []

    async def handler(task_id: str) -> HandlerResult:
        invoked.append(task_id)
        return HandlerResult(result_manifest={})

    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.ANALYSIS: handler},
    )
    await executor.run_until_idle()

    assert invoked == []
    with session_factory() as session:
        queued = session.get(Task, "still-queued")
        assert queued is not None and queued.status is TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_analysis_writes_deterministic_mock_report_and_events(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    _task(session_factory, settings, "analysis-a", TaskType.ANALYSIS)
    _task(session_factory, settings, "analysis-b", TaskType.ANALYSIS)
    different = _task(session_factory, settings, "analysis-c", TaskType.ANALYSIS)
    assert different.input_path is not None
    Path(different.input_path).write_bytes(b"DIFFERENT DETERMINISTIC FITS")
    handler = AnalysisTaskHandler(session_factory, settings, clock=lambda: FIXED_NOW)

    first = await handler.run("analysis-a")
    second = await handler.run("analysis-a")
    same_source = await handler.run("analysis-b")
    different_source = await handler.run("analysis-c")

    report_path = settings.data_root / "tasks" / "analysis-a" / "analysis-report.json"
    report = json.loads(report_path.read_text())
    same_source_report = json.loads(
        (settings.data_root / "tasks" / "analysis-b" / "analysis-report.json").read_text()
    )
    assert first == second
    assert report == same_source_report
    assert (
        first.result_manifest["summary"]["professional_metrics"]
        == same_source.result_manifest["summary"]["professional_metrics"]
    )
    assert (
        first.result_manifest["summary"]["recommendations"]
        == same_source.result_manifest["summary"]["recommendations"]
    )
    assert (
        first.result_manifest["summary"]["professional_metrics"]
        != different_source.result_manifest["summary"]["professional_metrics"]
    )
    assert report["demo"] is True
    assert report["notice"] == "Mock/demo analysis; not a scientific measurement."
    assert report["inspection"]["statistics"]["median"] == 0.35
    assert report["input_metadata"]["size_bytes"] == len(b"SIMPLE DETERMINISTIC FITS")
    assert len(report["input_metadata"]["sha256"]) == 64
    assert set(report["professional_metrics"]) == {
        "ellipticity",
        "fwhm",
        "snr",
        "star_count",
    }
    assert report["recommendations"]
    assert report["parameter_plan"]
    assert first.result_manifest["artifacts"][0]["name"] == "analysis-report.json"
    assert [event.event_type for event in _events(session_factory, "analysis-a")] == [
        "analysis_started",
        "analysis_metrics_generated",
        "analysis_report_written",
        "analysis_started",
        "analysis_metrics_generated",
        "analysis_report_written",
    ]


@pytest.mark.asyncio
async def test_processing_invokes_agent_and_persists_exact_artifacts(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    _task(
        session_factory,
        settings,
        "processing",
        TaskType.PROCESSING,
        style=ProcessingStyle.BALANCED,
    )

    result = await ProcessingTaskHandler(
        session_factory,
        settings,
        clock=lambda: FIXED_NOW,
    ).run("processing")

    assert [entry["name"] for entry in result.result_manifest["artifacts"]] == [
        "result-demo.tiff",
        "preview-demo.png",
        "manifest.json",
    ]
    assert result.result_manifest["demo"] is True
    assert result.result_manifest["summary"]["demo"] is True
    task_dir = settings.data_root / "tasks" / "processing"
    assert {path.name for path in task_dir.iterdir()} == {
        "source.fits",
        "result-demo.tiff",
        "preview-demo.png",
        "manifest.json",
    }
    assert [event.event_type for event in _events(session_factory, "processing")] == [
        "agent_plan",
        "agent_tool_started",
        "agent_tool_finished",
        "agent_tool_started",
        "agent_tool_finished",
        "agent_tool_started",
        "agent_tool_finished",
        "agent_tool_started",
        "agent_tool_finished",
        "agent_tool_started",
        "agent_tool_finished",
        "agent_tool_started",
        "agent_tool_finished",
        "agent_tool_started",
        "agent_tool_finished",
        "agent_evaluation",
        "agent_completion",
    ]


@pytest.mark.asyncio
async def test_processing_persists_tool_events_before_runner_completes(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    settings.mock_agent_step_delay_seconds = 0.2
    _task(
        session_factory,
        settings,
        "processing-incremental-events",
        TaskType.PROCESSING,
        style=ProcessingStyle.BALANCED,
    )
    running = asyncio.create_task(
        ProcessingTaskHandler(session_factory, settings).run(
            "processing-incremental-events"
        )
    )

    for _ in range(50):
        event_types = [
            event.event_type
            for event in _events(session_factory, "processing-incremental-events")
        ]
        if "agent_tool_started" in event_types:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("first agent tool event was not persisted during execution")

    assert running.done() is False
    assert "agent_completion" not in event_types
    await running

    final_types = [
        event.event_type
        for event in _events(session_factory, "processing-incremental-events")
    ]
    assert final_types.count("agent_tool_started") == 7
    assert final_types[-1] == "agent_completion"


@pytest.mark.asyncio
async def test_processing_event_sink_failure_uses_executor_failure_path(
    session_factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(
        session_factory,
        settings,
        "processing-event-store-failure",
        TaskType.PROCESSING,
        style=ProcessingStyle.BALANCED,
    )
    handler = ProcessingTaskHandler(session_factory, settings)

    def fail_event_persistence(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("event store unavailable")

    monkeypatch.setattr(handler._events, "append", fail_event_persistence)
    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.PROCESSING: handler.run},
        clock=lambda: FIXED_NOW,
    )

    await executor.run_until_idle()

    with session_factory() as session:
        persisted = session.get(Task, task.id)
        assert persisted is not None
        assert persisted.status is TaskStatus.FAILED
        assert persisted.error_code == "system_error"
        assert persisted.result_manifest is None
    task_dir = settings.data_root / "tasks" / task.id
    assert not (task_dir / "result-demo.tiff").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("symlink_target", ["data_root", "tasks", "task_dir"])
async def test_processing_rejects_symlinked_output_components(
    session_factory: sessionmaker[Session],
    settings: Settings,
    tmp_path: Path,
    symlink_target: str,
) -> None:
    task = _task(
        session_factory,
        settings,
        f"processing-symlink-{symlink_target}",
        TaskType.PROCESSING,
        style=ProcessingStyle.BALANCED,
    )
    outside = tmp_path / f"outside-{symlink_target}"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"unchanged")
    tasks = settings.data_root / "tasks"
    if symlink_target == "data_root":
        moved_root = tmp_path / "moved-data-root"
        settings.data_root.rename(moved_root)
        settings.data_root.symlink_to(outside, target_is_directory=True)
    elif symlink_target == "tasks":
        tasks.symlink_to(outside, target_is_directory=True)
    else:
        tasks.mkdir()
        (tasks / task.id).symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        await ProcessingTaskHandler(session_factory, settings).run(task.id)

    assert sentinel.read_bytes() == b"unchanged"
    assert set(outside.iterdir()) == {sentinel}


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", [TaskType.ANALYSIS, TaskType.PROCESSING])
async def test_timeout_maps_to_stable_failure(
    session_factory: sessionmaker[Session],
    settings: Settings,
    task_type: TaskType,
) -> None:
    _task(session_factory, settings, f"timeout-{task_type.value}", task_type)
    short_settings = settings.model_copy(
        update={
            "analysis_timeout_seconds": 0.01,
            "processing_timeout_seconds": 0.01,
        }
    )

    async def hangs(_task_id: str) -> HandlerResult:
        await asyncio.sleep(1)
        raise AssertionError("unreachable")

    executor = SerialTaskExecutor(
        session_factory,
        short_settings,
        handlers={task_type: hangs},
        clock=lambda: FIXED_NOW,
    )
    await executor.run_until_idle()

    with session_factory() as session:
        task = session.get(Task, f"timeout-{task_type.value}")
        assert task is not None
        assert task.status is TaskStatus.FAILED
        assert task.error_code == "task_timeout"
        assert task.retryable is True
        assert task.finished_at == FIXED_NOW
        assert task.expires_at == FIXED_NOW + timedelta(hours=24)
    assert _events(session_factory, f"timeout-{task_type.value}")[-1].event_type == "task_timeout"


@pytest.mark.asyncio
async def test_cancellation_before_start_and_during_handler(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    before = _task(session_factory, settings, "cancel-before", TaskType.ANALYSIS)
    with session_factory() as session:
        row = session.get(Task, before.id)
        assert row is not None
        row.status = TaskStatus.CANCELLING
        row.cancel_requested_at = FIXED_NOW
        session.commit()
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(task_id: str) -> HandlerResult:
        started.set()
        await release.wait()
        return HandlerResult(result_manifest={"task_id": task_id})

    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.ANALYSIS: handler},
        clock=lambda: FIXED_NOW,
    )
    await executor.run_until_idle()
    with session_factory() as session:
        row = session.get(Task, before.id)
        assert row is not None and row.status is TaskStatus.CANCELLED
    assert not started.is_set()

    _task(session_factory, settings, "cancel-during", TaskType.ANALYSIS)
    running = asyncio.create_task(executor.run_until_idle())
    await started.wait()
    with session_factory() as session:
        row = session.get(Task, "cancel-during")
        assert row is not None
        row.status = TaskStatus.CANCELLING
        row.cancel_requested_at = FIXED_NOW
        session.commit()
    release.set()
    await running
    with session_factory() as session:
        row = session.get(Task, "cancel-during")
        assert row is not None
        assert row.status is TaskStatus.CANCELLED
        assert row.finished_at == FIXED_NOW
        assert row.expires_at == FIXED_NOW + timedelta(hours=24)


@pytest.mark.asyncio
async def test_cancellation_wins_when_requested_before_completion_commit(
    session_factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _task(session_factory, settings, "cancel-completion-race", TaskType.ANALYSIS)

    async def handler(_task_id: str) -> HandlerResult:
        return HandlerResult(result_manifest={"should_not_commit": True})

    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.ANALYSIS: handler},
        clock=lambda: FIXED_NOW,
    )
    original_finish = executor._finish_completed

    def request_cancel_then_finish(task_id: str, result: HandlerResult) -> None:
        with session_factory() as session:
            task = session.get(Task, task_id)
            assert task is not None
            task.status = TaskStatus.CANCELLING
            task.cancel_requested_at = FIXED_NOW
            session.commit()
        original_finish(task_id, result)

    monkeypatch.setattr(executor, "_finish_completed", request_cancel_then_finish)

    await executor.run_until_idle()

    with session_factory() as session:
        task = session.get(Task, "cancel-completion-race")
        assert task is not None
        assert task.status is TaskStatus.CANCELLED
        assert task.result_manifest is None
    assert [event.event_type for event in _events(session_factory, task.id)][-1] == "task_cancelled"


@pytest.mark.asyncio
async def test_cancellation_wins_when_requested_before_failure_commit(
    session_factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _task(session_factory, settings, "cancel-failure-race", TaskType.ANALYSIS)

    async def handler(_task_id: str) -> HandlerResult:
        raise TaskHandlerError("handler_failed", "Handler failed.", False)

    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.ANALYSIS: handler},
        clock=lambda: FIXED_NOW,
    )
    original_finish = executor._finish_failed

    def request_cancel_then_finish(
        task_id: str,
        error_code: str,
        error_message: str,
        *,
        retryable: bool,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with session_factory() as session:
            task = session.get(Task, task_id)
            assert task is not None
            task.status = TaskStatus.CANCELLING
            task.cancel_requested_at = FIXED_NOW
            session.commit()
        original_finish(
            task_id,
            error_code,
            error_message,
            retryable=retryable,
            event_type=event_type,
            payload=payload,
        )

    monkeypatch.setattr(executor, "_finish_failed", request_cancel_then_finish)

    await executor.run_until_idle()

    with session_factory() as session:
        task = session.get(Task, "cancel-failure-race")
        assert task is not None
        assert task.status is TaskStatus.CANCELLED
        assert task.error_code == "user_cancelled"
    assert [event.event_type for event in _events(session_factory, task.id)][-1] == "task_cancelled"


@pytest.mark.asyncio
async def test_processing_cancellation_callback_stops_agent(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    _task(
        session_factory,
        settings,
        "cancel-agent",
        TaskType.PROCESSING,
        style=ProcessingStyle.REALISTIC,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingRunner:
        async def run(self, context: Any) -> None:
            entered.set()
            await release.wait()
            assert context.cancellation_check() is True
            raise AgentCancelledError()

    handler = ProcessingTaskHandler(
        session_factory,
        settings,
        runner_factory=lambda _store, _event_sink: BlockingRunner(),
    )
    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.PROCESSING: handler.run},
        clock=lambda: FIXED_NOW,
    )
    running = asyncio.create_task(executor.run_until_idle())
    await entered.wait()
    with session_factory() as session:
        task = session.get(Task, "cancel-agent")
        assert task is not None
        task.status = TaskStatus.CANCELLING
        task.cancel_requested_at = FIXED_NOW
        session.commit()
    release.set()
    await running

    with session_factory() as session:
        task = session.get(Task, "cancel-agent")
        assert task is not None
        assert task.status is TaskStatus.CANCELLED
        assert task.error_code == "user_cancelled"


@pytest.mark.asyncio
async def test_unexpected_exception_is_logged_and_stored_safely(
    session_factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _task(session_factory, settings, "broken", TaskType.ANALYSIS)
    logged: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "app.tasks.executor.logger.exception",
        lambda *args, **_kwargs: logged.append(args),
    )

    async def broken(_task_id: str) -> HandlerResult:
        raise RuntimeError("secret-path=/private/source.fit")

    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.ANALYSIS: broken},
        clock=lambda: FIXED_NOW,
    )
    await executor.run_until_idle()

    with session_factory() as session:
        task = session.get(Task, "broken")
        assert task is not None
        assert task.status is TaskStatus.FAILED
        assert task.error_code == "system_error"
        assert task.retryable is True
        assert task.error_message is not None
        assert task.error_message.startswith("diagnostic_id=")
        assert "private" not in task.error_message
    assert logged
    assert logged[0][0] == "Task %s failed; diagnostic_id=%s"


def test_startup_recovery_marks_only_unfinished_tasks(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    for status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLING):
        _task(session_factory, settings, status.value, TaskType.ANALYSIS, status=status)
    _task(
        session_factory,
        settings,
        "completed",
        TaskType.ANALYSIS,
        status=TaskStatus.COMPLETED,
    )

    recovered = recover_interrupted_tasks(
        session_factory,
        clock=lambda: FIXED_NOW,
    )

    assert recovered == 3
    with session_factory() as session:
        for task_id in ("queued", "running", "cancelling"):
            task = session.get(Task, task_id)
            assert task is not None
            assert task.status is TaskStatus.FAILED
            assert task.error_code == "restart_interrupted"
            assert task.retryable is True
            assert task.finished_at == FIXED_NOW
            assert task.expires_at == FIXED_NOW + timedelta(hours=24)
        completed = session.get(Task, "completed")
        assert completed is not None and completed.status is TaskStatus.COMPLETED
    assert all(
        _events(session_factory, task_id)[-1].event_type == "restart_interrupted"
        for task_id in ("queued", "running", "cancelling")
    )


@pytest.mark.asyncio
async def test_startup_recovery_finalizes_running_delete_without_handler(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    task = _task(
        session_factory,
        settings,
        "restart-delete",
        TaskType.PROCESSING,
        status=TaskStatus.RUNNING,
        style=ProcessingStyle.BALANCED,
    )
    source = Path(task.input_path or "")
    task_dir = settings.data_root / "tasks" / task.id
    task_dir.mkdir(parents=True)
    (task_dir / "partial.tiff").write_bytes(b"partial")
    TaskEventService(session_factory, clock=lambda: FIXED_NOW).append(
        task.id,
        EventLevel.INFO,
        "task_started",
        {"private": True},
    )
    with session_factory() as session:
        persisted = session.get(Task, task.id)
        assert persisted is not None
        persisted.delete_requested_at = FIXED_NOW
        persisted.cancel_requested_at = FIXED_NOW
        persisted.cleanup_pending = True
        persisted.cleanup_plan = {
            "version": 1,
            "task_dir": ["tasks", task.id],
            "source_file": ["uploads", task.id, "input.fits"],
        }
        session.commit()

    recovered = recover_interrupted_tasks(session_factory, clock=lambda: FIXED_NOW)
    handler_calls = 0

    async def handler(_task_id: str) -> HandlerResult:
        nonlocal handler_calls
        handler_calls += 1
        return HandlerResult(result_manifest={"should_not_run": True})

    executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={TaskType.PROCESSING: handler},
        clock=lambda: FIXED_NOW,
    )
    await executor.run_until_idle()

    assert recovered == 1
    assert handler_calls == 0
    assert not source.exists()
    assert not task_dir.exists()
    assert _events(session_factory, task.id) == []
    with session_factory() as session:
        deleted = session.get(Task, task.id)
        assert deleted is not None
        assert deleted.status is TaskStatus.EXPIRED
        assert deleted.error_code == "user_deleted"
        assert deleted.cleanup_pending is False
        assert deleted.cleanup_error is None
        assert deleted.cleanup_plan is None
        assert deleted.input_path is None
        assert deleted.result_manifest is None
        assert deleted.stage is None


@pytest.mark.asyncio
async def test_startup_cleanup_failure_stays_pending_and_next_restart_retries(
    session_factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task(
        session_factory,
        settings,
        "restart-delete-retry",
        TaskType.ANALYSIS,
        status=TaskStatus.RUNNING,
    )
    task_dir = settings.data_root / "tasks" / task.id
    task_dir.mkdir(parents=True)
    (task_dir / "partial.json").write_bytes(b"partial")
    with session_factory() as session:
        persisted = session.get(Task, task.id)
        assert persisted is not None
        persisted.delete_requested_at = FIXED_NOW
        persisted.cancel_requested_at = FIXED_NOW
        persisted.cleanup_pending = True
        persisted.cleanup_plan = {
            "version": 1,
            "task_dir": ["tasks", task.id],
            "source_file": ["uploads", task.id, "input.fits"],
        }
        session.commit()

    real_rmtree = shutil.rmtree
    attempts = 0

    def fail_once(path: Path | str, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError(f"cannot remove {path}")
        real_rmtree(path, **kwargs)

    monkeypatch.setattr("app.tasks.service.shutil.rmtree", fail_once)

    recover_interrupted_tasks(session_factory, clock=lambda: FIXED_NOW)
    first_executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={},
        clock=lambda: FIXED_NOW,
    )
    await first_executor.run_until_idle()

    with session_factory() as session:
        pending = session.get(Task, task.id)
        assert pending is not None
        assert pending.status is TaskStatus.EXPIRED
        assert pending.error_code == "user_deleted"
        assert pending.cleanup_pending is True
        assert pending.cleanup_error is not None
        assert str(settings.data_root) not in pending.cleanup_error
        assert pending.input_path is not None
        assert pending.cleanup_plan is not None
    assert task_dir.exists()

    recover_interrupted_tasks(session_factory, clock=lambda: FIXED_NOW)
    second_executor = SerialTaskExecutor(
        session_factory,
        settings,
        handlers={},
        clock=lambda: FIXED_NOW,
    )
    await second_executor.run_until_idle()

    assert attempts == 2
    assert not task_dir.exists()
    with session_factory() as session:
        deleted = session.get(Task, task.id)
        assert deleted is not None
        assert deleted.status is TaskStatus.EXPIRED
        assert deleted.error_code == "user_deleted"
        assert deleted.cleanup_pending is False
        assert deleted.cleanup_error is None


def test_startup_recovery_rolls_back_states_when_event_insert_fails(
    session_factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLING):
        _task(
            session_factory,
            settings,
            f"rollback-{status.value}",
            TaskType.ANALYSIS,
            status=status,
        )

    def fail_event(*_args: object, **_kwargs: object) -> TaskEvent:
        raise _permanent_db_error("recovery event insert failed")

    monkeypatch.setattr(TaskEventService, "append_in_session", fail_event)

    with pytest.raises(IntegrityError, match="recovery event insert failed"):
        recover_interrupted_tasks(session_factory, clock=lambda: FIXED_NOW)

    with session_factory() as session:
        assert session.get(Task, "rollback-queued").status is TaskStatus.QUEUED
        assert session.get(Task, "rollback-running").status is TaskStatus.RUNNING
        assert session.get(Task, "rollback-cancelling").status is TaskStatus.CANCELLING
    assert all(
        _events(session_factory, task_id) == []
        for task_id in (
            "rollback-queued",
            "rollback-running",
            "rollback-cancelling",
        )
    )


def test_worker_lifespan_starts_and_stops_without_leaking_tasks(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    created: list[SerialTaskExecutor] = []

    def executor_factory() -> SerialTaskExecutor:
        executor = SerialTaskExecutor(session_factory, settings, poll_interval_seconds=0.01)
        created.append(executor)
        return executor

    test_app = FastAPI(lifespan=build_lifespan(executor_factory=executor_factory))
    with TestClient(test_app):
        assert created[0].is_running
    assert not created[0].is_running
    assert created[0].worker_task is None


def test_app_lifespan_runs_with_request_db_override(
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    _task(session_factory, settings, "recover-with-override", TaskType.ANALYSIS)
    test_app = create_app(session_factory=session_factory, settings=settings)

    def override_db_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_db_session] = override_db_session

    with TestClient(test_app):
        executor = test_app.state.task_executor
        assert executor.is_running
        with session_factory() as session:
            task = session.get(Task, "recover-with-override")
            assert task is not None
            assert task.status is TaskStatus.FAILED
            assert task.error_code == "restart_interrupted"

    assert not executor.is_running
    assert executor.worker_task is None
