import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.agent_sdk import (
    AgentGuardrailError,
    AgentNotConfiguredError,
    AgentProviderError,
    AgentRunCancelled,
    AgentSdkBridge,
    SkillExecutionError,
    SkillOutputError,
)
from app.artifacts.store import ArtifactStore
from app.config import Settings
from app.db.models import EventLevel, ProcessingStyle, Task, TaskStatus, TaskType
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
        bridge: AgentSdkBridge | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._events = TaskEventService(session_factory, clock=clock)
        self._bridge = bridge or AgentSdkBridge(settings)

    async def run(self, task_id: str) -> HandlerResult:
        task, inspection, source_path = self._load_task(task_id)
        self._check_cancelled(task_id)
        self._set_stage(task_id, "agent_preparing", 10)
        task_dir = self._settings.data_root / "tasks" / task_id
        data_root_fd = open_directory_fd(self._settings.data_root, create=True)
        try:
            task_dir_fd = open_relative_directory_fd(
                data_root_fd,
                ("tasks", task_id),
                create=True,
            )
            try:
                self._set_stage(task_id, "agent_running", 25)
                spec = self._bridge.build_analysis_spec(
                    task_id=task.id,
                    source_path=source_path,
                    inspection=inspection,
                )
                persist_event = self._event_sink(task_id)
                with ArtifactStore.from_directory_fd(task_dir, task_dir_fd) as store:
                    run = await self._bridge.run(
                        spec,
                        artifact_store=store,
                        cancellation_check=lambda: self._cancel_requested(task_id),
                        event_sink=persist_event,
                    )
            finally:
                os.close(task_dir_fd)
        except AgentRunCancelled as exc:
            raise TaskCancelled() from exc
        except (
            AgentNotConfiguredError,
            AgentProviderError,
            AgentGuardrailError,
            SkillExecutionError,
            SkillOutputError,
        ) as exc:
            raise _task_handler_error(exc) from exc
        finally:
            os.close(data_root_fd)

        self._check_cancelled(task_id)
        self._set_stage(task_id, "agent_complete", 90)
        return HandlerResult(
            result_manifest={
                "artifacts": [
                    artifact.model_dump(mode="json")
                    for artifact in run.artifacts
                ],
                "inspection": inspection.model_dump(mode="json"),
                "summary": run.summary,
                "demo": False,
            }
        )

    def _load_task(self, task_id: str) -> tuple[Task, FitsInspection, Path]:
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
            return task, FitsInspection.model_validate(raw_inspection), source_path

    def _check_cancelled(self, task_id: str) -> None:
        if self._cancel_requested(task_id):
            raise TaskCancelled()

    def _cancel_requested(self, task_id: str) -> bool:
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            return task is None or _is_cancelled(task)

    def _event_sink(
        self,
        task_id: str,
    ) -> Callable[[str, dict[str, object]], None]:
        sequence = 0

        def persist(event_type: str, payload: dict[str, object]) -> None:
            nonlocal sequence
            sequence += 1
            self._events.append(
                task_id,
                EventLevel.INFO,
                f"agent_{event_type}",
                {"agent_sequence": sequence, **payload},
            )

        return persist

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
        bridge: AgentSdkBridge | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._events = TaskEventService(session_factory, clock=clock)
        self._bridge = bridge or AgentSdkBridge(settings)

    async def run(self, task_id: str) -> HandlerResult:
        task, inspection = self._load_task(task_id)
        if self._cancel_requested(task_id):
            raise TaskCancelled()
        if inspection is None:
            raise ValueError("processing FITS inspection is missing")
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
                copy_regular_file_at(
                    data_root_fd,
                    relative_path_components(
                        self._settings.data_root,
                        Path(task.input_path),
                    ),
                    task_dir_fd,
                    source_path.name,
                )
                self._set_stage(task_id, "agent_running", 25)
                spec = self._bridge.build_processing_spec(
                    task_id=task.id,
                    source_path=source_path,
                    inspection=inspection,
                    style=task.style or ProcessingStyle.BALANCED,
                )
                persist_event = self._event_sink(task_id)
                with ArtifactStore.from_directory_fd(task_dir, task_dir_fd) as store:
                    run = await self._bridge.run(
                        spec,
                        artifact_store=store,
                        cancellation_check=lambda: self._cancel_requested(task_id),
                        event_sink=persist_event,
                    )
            finally:
                os.close(task_dir_fd)
        except AgentRunCancelled as exc:
            raise TaskCancelled() from exc
        except (
            AgentNotConfiguredError,
            AgentProviderError,
            AgentGuardrailError,
            SkillExecutionError,
            SkillOutputError,
        ) as exc:
            raise _task_handler_error(exc) from exc
        finally:
            os.close(data_root_fd)

        self._set_stage(task_id, "agent_complete", 90)
        manifest: dict[str, Any] = {
            "artifacts": [
                artifact.model_dump(mode="json")
                for artifact in run.artifacts
            ],
            "summary": run.summary,
            "inspection": inspection.model_dump(mode="json"),
            "demo": False,
        }
        if run.quality_score is not None:
            manifest["quality_score"] = run.quality_score
        return HandlerResult(result_manifest=manifest)

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

    def _event_sink(
        self,
        task_id: str,
    ) -> Callable[[str, dict[str, object]], None]:
        sequence = 0

        def persist(event_type: str, payload: dict[str, object]) -> None:
            nonlocal sequence
            sequence += 1
            self._events.append(
                task_id,
                EventLevel.INFO,
                f"agent_{event_type}",
                {"agent_sequence": sequence, **payload},
            )

        return persist

    def _set_stage(self, task_id: str, stage: str, progress: int) -> None:
        with self._session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("task not found")
            task.stage = stage
            task.progress = progress
            session.commit()


def _task_handler_error(error: Exception) -> TaskHandlerError:
    if isinstance(error, AgentNotConfiguredError):
        return TaskHandlerError("agent_not_configured", str(error), False)
    if isinstance(error, AgentProviderError):
        return TaskHandlerError("agent_provider_error", str(error), error.retryable)
    if isinstance(error, AgentGuardrailError):
        return TaskHandlerError("agent_guardrail", "Agent output was rejected.", False)
    if isinstance(error, SkillExecutionError):
        return TaskHandlerError("skill_execution_failed", str(error), error.retryable)
    if isinstance(error, SkillOutputError):
        return TaskHandlerError("skill_output_invalid", str(error), False)
    raise TypeError(f"unsupported Agent SDK error: {type(error).__name__}")


def _is_cancelled(task: Task) -> bool:
    return task.cancel_requested_at is not None or task.status is TaskStatus.CANCELLING
