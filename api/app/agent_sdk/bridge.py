import asyncio
import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path

from agents.exceptions import (
    InputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    ModelBehaviorError,
    OutputGuardrailTripwireTriggered,
)
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from app.agent_sdk.agents import build_analysis_agent, build_processing_agent
from app.agent_sdk.artifacts import publish_claimed_artifacts
from app.agent_sdk.contracts import (
    AnalysisSkillResult,
    ProcessingSkillResult,
    PublishedSkillRun,
    SkillRequest,
)
from app.agent_sdk.errors import (
    AgentGuardrailError,
    AgentProviderError,
    AgentRunCancelled,
    SkillExecutionError,
    SkillOutputError,
)
from app.agent_sdk.providers import build_agent_model
from app.agent_sdk.runtime import OpenAiSandboxRuntime
from app.agent_sdk.runtime_types import AgentSdkRunSpec, AgentSdkRuntime
from app.agent_sdk.workspaces import SkillDefinition, build_task_manifest
from app.artifacts.store import ArtifactStore
from app.config import Settings
from app.db.models import ProcessingStyle, TaskType
from app.fits.schemas import FitsInspection
ARTWORK_DISCLAIMER = (
    "AI 自动出图为基于原始预览图生成的艺术增强图，不是可用于测光、科研或严格真实性"
    "验证的线性后期结果。"
)

BridgeEventSink = Callable[
    [str, dict[str, object]],
    Awaitable[None] | None,
]


class AgentSdkBridge:
    def __init__(
        self,
        settings: Settings,
        *,
        runtime_factory: Callable[[AgentSdkRunSpec], AgentSdkRuntime] | None = None,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        self._settings = settings
        self._runtime_factory = runtime_factory or self._default_runtime
        self._poll_interval_seconds = poll_interval_seconds

    def build_analysis_spec(
        self,
        *,
        task_id: str,
        source_path: Path,
        inspection: FitsInspection,
    ) -> AgentSdkRunSpec:
        request = SkillRequest(task_id=task_id, task_type=TaskType.ANALYSIS)
        manifest = build_task_manifest(
            source_path=source_path,
            inspection=inspection,
            request=request,
        )
        skill = SkillDefinition("deep-sky-advisor", self._settings.analysis_skill_path)
        agent = build_analysis_agent(build_agent_model(self._settings), skill, manifest)
        return AgentSdkRunSpec(
            task_id=task_id,
            task_type=TaskType.ANALYSIS,
            style=None,
            agent_name=agent.name,
            skill_name=skill.name,
            result_path="output/analysis-result.json",
            max_turns=self._settings.agent_max_turns,
            agent=agent,
            manifest=manifest,
            input_text=(
                "读取 input/request.json，使用 deep-sky-advisor skill 完成专业分析，"
                "并写出 output/analysis-result.json。"
            ),
        )

    def build_processing_spec(
        self,
        *,
        task_id: str,
        source_path: Path,
        inspection: FitsInspection,
        style: ProcessingStyle,
    ) -> AgentSdkRunSpec:
        request = SkillRequest(
            task_id=task_id,
            task_type=TaskType.PROCESSING,
            style=style,
        )
        manifest = build_task_manifest(
            source_path=source_path,
            inspection=inspection,
            request=request,
        )
        skill = SkillDefinition(
            "deep-sky-processor",
            self._settings.processing_skill_path,
        )
        agent = build_processing_agent(build_agent_model(self._settings), skill, manifest)
        return AgentSdkRunSpec(
            task_id=task_id,
            task_type=TaskType.PROCESSING,
            style=style,
            agent_name=agent.name,
            skill_name=skill.name,
            result_path="output/processing-result.json",
            max_turns=self._settings.agent_max_turns,
            agent=agent,
            manifest=manifest,
            input_text=(
                "读取 input/request.json，使用 deep-sky-processor skill 完成自动出图，"
                "并写出 output/processing-result.json。"
            ),
        )

    async def run(
        self,
        spec: AgentSdkRunSpec,
        *,
        artifact_store: ArtifactStore,
        cancellation_check: Callable[[], bool],
        event_sink: BridgeEventSink,
    ) -> PublishedSkillRun:
        runtime = self._runtime_factory(spec)

        async def emit(event_type: str, payload: dict[str, object]) -> None:
            result = event_sink(event_type, payload)
            if inspect.isawaitable(result):
                await result

        task = asyncio.create_task(runtime.run(spec, emit))
        try:
            while not task.done():
                if cancellation_check():
                    task.cancel()
                    raise AgentRunCancelled("agent_run_cancelled")
                await asyncio.sleep(self._poll_interval_seconds)
            try:
                await task
            except (MaxTurnsExceeded, ModelBehaviorError) as exc:
                raise AgentGuardrailError("Agent run was rejected.") from exc
            except (
                InputGuardrailTripwireTriggered,
                OutputGuardrailTripwireTriggered,
            ) as exc:
                raise AgentGuardrailError("Agent guardrail rejected the run.") from exc
            except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
                raise AgentProviderError(str(exc), retryable=True) from exc
            except APIStatusError as exc:
                raise AgentProviderError(
                    str(exc),
                    retryable=exc.status_code == 429 or exc.status_code >= 500,
                ) from exc
            except OSError as exc:
                raise SkillExecutionError(str(exc), retryable=False) from exc
            result = await self._read_and_publish(runtime, spec, artifact_store)
            await emit("run_completed", {"artifact_count": len(result.artifacts)})
            return result
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runtime.close()
            await runtime.delete()

    def _default_runtime(self, spec: AgentSdkRunSpec) -> AgentSdkRuntime:
        return OpenAiSandboxRuntime(spec)

    async def _read_and_publish(
        self,
        runtime: AgentSdkRuntime,
        spec: AgentSdkRunSpec,
        artifact_store: ArtifactStore,
    ) -> PublishedSkillRun:
        try:
            raw = await runtime.read_bytes(spec.result_path)
        except FileNotFoundError as exc:
            raise SkillExecutionError("Skill did not write its result file.") from exc
        if len(raw) > 64 * 1024:
            raise SkillOutputError("Skill result exceeds 64 KiB.")
        try:
            if spec.task_type is TaskType.ANALYSIS:
                result = AnalysisSkillResult.model_validate_json(raw, strict=True)
                artifacts = await publish_claimed_artifacts(
                    runtime,
                    artifact_store,
                    result.artifacts,
                )
                return PublishedSkillRun(
                    artifacts=artifacts,
                    summary={
                        "provider": result.provider,
                        "model": result.model,
                        "preview": result.preview.model_dump(mode="json"),
                        "analysis": result.analysis.model_dump(mode="json"),
                    },
                )
            processing_result = ProcessingSkillResult.model_validate_json(raw, strict=True)
            artifacts = await publish_claimed_artifacts(
                runtime,
                artifact_store,
                processing_result.artifacts,
            )
            return PublishedSkillRun(
                artifacts=artifacts,
                quality_score=processing_result.quality_score,
                summary={
                    "mode": "generative_art_enhancement",
                    "demo": False,
                    "style": processing_result.style.value,
                    "provider": processing_result.provider,
                    "model": processing_result.model,
                    "target_summary": processing_result.target_summary,
                    "visible_subject": processing_result.visible_subject,
                    "art_direction_summary": processing_result.art_direction_summary,
                    "reference_artifact": processing_result.reference_artifact,
                    "result_artifact": processing_result.result_artifact,
                    "result_width": processing_result.result_width or 0,
                    "result_height": processing_result.result_height or 0,
                    "provider_request_id": processing_result.provider_request_id,
                    "disclaimer": ARTWORK_DISCLAIMER,
                },
            )
        except ValueError as exc:
            raise SkillOutputError("Skill returned invalid structured output.") from exc
