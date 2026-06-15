import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.agent import build_mock_runner
from app.agent.contracts import TaskContext
from app.agent.contracts import AgentEvent
from app.agent.runner import AgentCancelledError, AgentGuardrailError, EventSink
from app.artifacts.store import ArtifactStore
from app.config import Settings
from app.db.models import EventLevel, Task, TaskStatus, TaskType
from app.fits.schemas import FitsInspection
from app.filesystem import (
    copy_regular_file_at,
    open_directory_fd,
    open_relative_directory_fd,
    relative_path_components,
)
from app.tasks.events import TaskEventService
from app.tasks.executor import HandlerResult, TaskCancelled, TaskHandlerError


class AnalysisTaskHandler:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._events = TaskEventService(session_factory, clock=clock)

    async def run(self, task_id: str) -> HandlerResult:
        task, inspection, input_metadata = self._load_task(task_id)
        self._check_cancelled(task_id)
        self._set_stage(task_id, "analysis", 10)
        self._events.append(task_id, EventLevel.INFO, "analysis_started", {"demo": True})

        report = _analysis_report(task, inspection, input_metadata)
        self._check_cancelled(task_id)
        self._set_stage(task_id, "analysis_metrics", 60)
        self._events.append(
            task_id,
            EventLevel.INFO,
            "analysis_metrics_generated",
            {"demo": True, "metrics": report["professional_metrics"]},
        )

        task_dir = self._settings.data_root / "tasks" / task_id
        data_root_fd = open_directory_fd(self._settings.data_root, create=True)
        try:
            task_dir_fd = open_relative_directory_fd(
                data_root_fd,
                ("tasks", task_id),
                create=True,
            )
            try:
                with ArtifactStore.from_directory_fd(task_dir, task_dir_fd) as store:
                    artifact = store.write_json("analysis-report.json", report)
            finally:
                os.close(task_dir_fd)
        finally:
            os.close(data_root_fd)
        self._check_cancelled(task_id)
        self._set_stage(task_id, "analysis_report", 90)
        self._events.append(
            task_id,
            EventLevel.INFO,
            "analysis_report_written",
            {"artifact": artifact.model_dump(mode="json"), "demo": True},
        )
        return HandlerResult(
            result_manifest={
                "artifacts": [artifact.model_dump(mode="json")],
                "inspection": inspection.model_dump(mode="json"),
                "summary": {
                    "demo": True,
                    "professional_metrics": report["professional_metrics"],
                    "recommendations": report["recommendations"],
                    "parameter_plan": report["parameter_plan"],
                },
                "demo": True,
            }
        )

    def _load_task(
        self,
        task_id: str,
    ) -> tuple[Task, FitsInspection, dict[str, Any]]:
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.type is not TaskType.ANALYSIS:
                raise ValueError("analysis task not found")
            raw_inspection = task.upload.validation_result if task.upload is not None else None
            if raw_inspection is None:
                raise ValueError("persisted FITS inspection is missing")
            if task.input_path is None:
                raise ValueError("analysis source path is missing")
            source_path = Path(task.input_path)
            if not source_path.is_file():
                raise ValueError("analysis source file is missing")
            return (
                task,
                FitsInspection.model_validate(raw_inspection),
                _source_metadata(source_path),
            )

    def _check_cancelled(self, task_id: str) -> None:
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or _is_cancelled(task):
                raise TaskCancelled()

    def _set_stage(self, task_id: str, stage: str, progress: int) -> None:
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("task not found")
            task.stage = stage
            task.progress = progress
            session.commit()


class ProcessingTaskHandler:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        runner_factory: Callable[[ArtifactStore, EventSink], Any] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._events = TaskEventService(session_factory, clock=clock)
        self._runner_factory = runner_factory or (
            lambda store, event_sink: build_mock_runner(
                store,
                step_delay_seconds=settings.mock_agent_step_delay_seconds,
                event_sink=event_sink,
            )
        )

    async def run(self, task_id: str) -> HandlerResult:
        task, inspection = self._load_task(task_id)
        if self._cancel_requested(task_id):
            raise TaskCancelled()
        task_dir = self._settings.data_root / "tasks" / task_id
        source_path = task_dir / "source.fits"
        if task.input_path is None:
            raise ValueError("task input path is missing")
        data_root_fd = open_directory_fd(self._settings.data_root, create=True)
        try:
            task_dir_fd = open_relative_directory_fd(
                data_root_fd,
                ("tasks", task_id),
                create=True,
            )
            try:
                input_size = copy_regular_file_at(
                    data_root_fd,
                    relative_path_components(
                        self._settings.data_root,
                        Path(task.input_path),
                    ),
                    task_dir_fd,
                    source_path.name,
                )
                self._set_stage(task_id, "agent_running", 10)

                context = TaskContext(
                    task_id=task.id,
                    task_type=task.type,
                    style=task.style,
                    task_dir=task_dir,
                    source_path=source_path,
                    fits_inspection=inspection,
                    basic_metadata={
                        "selected_hdu": task.selected_hdu,
                        "input_size": input_size,
                        "demo": True,
                    },
                    cancellation_check=lambda: self._cancel_requested(task_id),
                )
                def persist_event(event: AgentEvent) -> None:
                    self._events.append(
                        task_id,
                        EventLevel.INFO,
                        f"agent_{event.event_type}",
                        {"agent_sequence": event.sequence, **event.payload},
                        created_at=event.timestamp,
                    )

                with ArtifactStore.from_directory_fd(task_dir, task_dir_fd) as store:
                    result = await self._runner_factory(store, persist_event).run(context)
            finally:
                os.close(task_dir_fd)
        except AgentCancelledError as exc:
            raise TaskCancelled() from exc
        except AgentGuardrailError as exc:
            raise TaskHandlerError("agent_guardrail", "Agent output was rejected.", False) from exc
        finally:
            os.close(data_root_fd)

        self._set_stage(task_id, "agent_complete", 90)
        return HandlerResult(
            result_manifest={
                "artifacts": [artifact.model_dump(mode="json") for artifact in result.artifacts],
                "summary": result.summary,
                "quality_score": result.quality_score,
                "plan": result.plan.model_dump(mode="json"),
                "inspection": (
                    inspection.model_dump(mode="json") if inspection is not None else None
                ),
                "demo": True,
            }
        )

    def _load_task(self, task_id: str) -> tuple[Task, FitsInspection | None]:
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.type is not TaskType.PROCESSING:
                raise ValueError("processing task not found")
            raw_inspection: dict[str, Any] | None = None
            if task.upload is not None:
                raw_inspection = task.upload.validation_result
            elif task.source_task is not None and task.source_task.result_manifest is not None:
                candidate = task.source_task.result_manifest.get("inspection")
                if isinstance(candidate, dict):
                    raw_inspection = candidate
            inspection = (
                FitsInspection.model_validate(raw_inspection)
                if raw_inspection is not None
                else None
            )
            return task, inspection

    def _cancel_requested(self, task_id: str) -> bool:
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            return task is None or _is_cancelled(task)

    def _set_stage(self, task_id: str, stage: str, progress: int) -> None:
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("task not found")
            task.stage = stage
            task.progress = progress
            session.commit()


def _analysis_report(
    task: Task,
    inspection: FitsInspection,
    input_metadata: dict[str, Any],
) -> dict[str, Any]:
    metadata = {
        "style": task.style.value if task.style is not None else None,
        "input_metadata": input_metadata,
        "selected_hdu": task.selected_hdu,
        "inspection": inspection.model_dump(mode="json"),
    }
    digest = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    ).digest()
    metrics = {
        "snr": round(8.0 + int.from_bytes(digest[0:2], "big") / 65535 * 24.0, 3),
        "fwhm": round(1.5 + int.from_bytes(digest[2:4], "big") / 65535 * 3.0, 3),
        "ellipticity": round(0.05 + int.from_bytes(digest[4:6], "big") / 65535 * 0.35, 3),
        "star_count": 150 + int.from_bytes(digest[6:8], "big") % 4851,
    }
    return {
        "demo": True,
        "notice": "Mock/demo analysis; not a scientific measurement.",
        "input_metadata": input_metadata,
        "inspection": inspection.model_dump(mode="json"),
        "professional_metrics": metrics,
        "recommendations": [
            "Use a moderate nonlinear stretch while preserving the background.",
            "Apply conservative denoise before local sharpening.",
            "Verify color balance against the persisted FITS statistics.",
        ],
        "parameter_plan": {
            "stretch": round(0.8 + metrics["snr"] / 100, 3),
            "denoise": round(max(0.1, 0.5 - metrics["snr"] / 100), 3),
            "sharpen": round(max(0.1, 0.6 - metrics["fwhm"] / 10), 3),
            "saturation": 1.0,
        },
    }


def _is_cancelled(task: Task) -> bool:
    return task.cancel_requested_at is not None or task.status is TaskStatus.CANCELLING


def _source_metadata(source_path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with source_path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return {
        "file_name": source_path.name,
        "size_bytes": source_path.stat().st_size,
        "sha256": digest.hexdigest(),
    }
