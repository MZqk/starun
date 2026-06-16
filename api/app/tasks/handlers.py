import hashlib
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.agent.contracts import TaskContext
from app.agent.contracts import AgentEvent
from app.agent.runner import AgentCancelledError, AgentGuardrailError, EventSink
from app.analysis import (
    KimiAnalysisClient,
    KimiAnalysisError,
    KimiConfigurationError,
    render_fits_preview,
)
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
from app.processing import build_processing_runner
from app.processing.art_direction import KimiArtDirectionError
from app.processing.image_provider import (
    ImageProviderConfigurationError,
    ImageProviderError,
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
        _, inspection, input_metadata, source_path = self._load_task(task_id)
        self._check_cancelled(task_id)
        self._set_stage(task_id, "preview_generation", 10)
        self._events.append(task_id, EventLevel.INFO, "analysis_started", {})
        try:
            preview = render_fits_preview(source_path, inspection.selected_hdu.index)
        except (OSError, TypeError, ValueError, MemoryError) as exc:
            raise TaskHandlerError(
                "preview_generation_failed",
                "The FITS preview could not be generated.",
                False,
            ) from exc
        self._check_cancelled(task_id)
        self._set_stage(task_id, "ai_analysis", 35)
        self._events.append(
            task_id,
            EventLevel.INFO,
            "analysis_preview_generated",
            {"width": preview.width, "height": preview.height},
        )
        try:
            analysis = await KimiAnalysisClient(
                base_url=self._settings.ai_base_url,
                api_key=self._settings.ai_api_key,
                model=self._settings.ai_model,
                timeout_seconds=self._settings.ai_timeout_seconds,
            ).analyze(
                preview_png=preview.data,
                inspection=inspection,
                preview_metadata={
                    "width": preview.width,
                    "height": preview.height,
                    "lower_percentile_value": preview.lower_percentile,
                    "upper_percentile_value": preview.upper_percentile,
                },
            )
        except KimiConfigurationError as exc:
            raise TaskHandlerError("ai_not_configured", str(exc), False) from exc
        except KimiAnalysisError as exc:
            raise TaskHandlerError(
                "ai_provider_error",
                str(exc),
                exc.retryable,
            ) from exc

        report = {
            "provider": "kimi",
            "model": self._settings.ai_model,
            "input_metadata": input_metadata,
            "inspection": inspection.model_dump(mode="json"),
            "preview": {
                "artifact": "analysis-preview.png",
                "width": preview.width,
                "height": preview.height,
                "lower_percentile_value": preview.lower_percentile,
                "upper_percentile_value": preview.upper_percentile,
            },
            "analysis": analysis.model_dump(mode="json"),
        }
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
                    preview_artifact = store.write_bytes(
                        "analysis-preview.png",
                        preview.data,
                    )
                    report_artifact = store.write_json("analysis-report.json", report)
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
            {
                "artifacts": [
                    preview_artifact.model_dump(mode="json"),
                    report_artifact.model_dump(mode="json"),
                ],
                "provider": "kimi",
                "model": self._settings.ai_model,
            },
        )
        return HandlerResult(
            result_manifest={
                "artifacts": [
                    preview_artifact.model_dump(mode="json"),
                    report_artifact.model_dump(mode="json"),
                ],
                "inspection": inspection.model_dump(mode="json"),
                "summary": {
                    "provider": "kimi",
                    "model": self._settings.ai_model,
                    "preview": report["preview"],
                    "analysis": analysis.model_dump(mode="json"),
                },
                "demo": False,
            }
        )

    def _load_task(
        self,
        task_id: str,
    ) -> tuple[Task, FitsInspection, dict[str, Any], Path]:
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
                source_path,
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
            lambda store, event_sink: build_processing_runner(
                store,
                settings=settings,
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
        except KimiArtDirectionError as exc:
            raise TaskHandlerError("art_direction_failed", str(exc), exc.retryable) from exc
        except ImageProviderConfigurationError as exc:
            raise TaskHandlerError("image_provider_not_configured", str(exc), False) from exc
        except ImageProviderError as exc:
            raise TaskHandlerError(exc.code, str(exc), exc.retryable) from exc
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
                "demo": False,
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
