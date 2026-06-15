import asyncio
import inspect
import json
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agent.contracts import (
    AgentEvent,
    AgentPlan,
    AgentRunResult,
    JsonValue,
    ModelAdapter,
    TaskContext,
    Tool,
    ToolResult,
)
from app.agent.registry import ToolRegistry
from app.artifacts.store import ArtifactPathError, ArtifactStore

MAX_STEPS = 12
MAX_ITERATIONS = 2
MAX_ARTIFACT_COUNT = 16
MAX_OBSERVATION_JSON_BYTES = 64 * 1024
OccurrenceTimestampProvider = Callable[[TaskContext, int], datetime]
EventSink = Callable[[AgentEvent], Awaitable[None] | None]


class AgentGuardrailError(ValueError):
    pass


class InvalidToolArgumentsError(AgentGuardrailError):
    pass


class AgentOutputError(AgentGuardrailError):
    pass


class AgentCancelledError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("agent_run_cancelled")


class AgentRunner:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        registry: ToolRegistry,
        artifact_store: ArtifactStore,
        occurrence_timestamp_provider: OccurrenceTimestampProvider | None = None,
        step_delay_seconds: float = 0,
        event_sink: EventSink | None = None,
    ) -> None:
        if step_delay_seconds < 0:
            raise ValueError("step_delay_seconds must be nonnegative")
        self._model = model
        self._registry = registry
        self._artifact_store = artifact_store
        self._step_delay_seconds = step_delay_seconds
        self._event_sink = event_sink
        self._occurrence_timestamp_provider = (
            occurrence_timestamp_provider or utc_occurrence_timestamp
        )

    async def run(self, context: TaskContext) -> AgentRunResult:
        self._validate_context_paths(context)
        self._check_cancelled(context)
        raw_plan = await self._model.plan(context)
        self._check_cancelled(context)
        plan = self._validate_plan(raw_plan)
        prepared = self._prepare_steps(plan)
        events: list[AgentEvent] = []
        artifact_names: set[str] = set()
        observation_json_bytes = 0
        await self._event(
            context,
            events,
            "plan",
            {
                "version": plan.version,
                "step_count": len(plan.steps),
                "max_iterations": plan.max_iterations,
            },
        )
        combined = ToolResult()
        for step, tool, arguments in prepared:
            await self._delay_checkpoint(context)
            await self._event(
                context,
                events,
                "tool_started",
                {
                    "step_id": step.id,
                    "tool_name": step.tool_name,
                    "tool_version": step.tool_version,
                },
            )
            raw_result = await tool.execute(context, arguments)
            self._check_cancelled(context)
            result = self._validate_tool_result(raw_result)
            observation_json_bytes += self._serialized_json_size(result.observations)
            if observation_json_bytes > MAX_OBSERVATION_JSON_BYTES:
                raise AgentOutputError("agent observations exceed maximum serialized size")
            self._verify_artifacts(result, artifact_names)
            combined.observations[step.id] = result.observations
            combined.artifacts.extend(result.artifacts)
            combined.metrics.update(result.metrics)
            event_metrics: dict[str, JsonValue] = {
                name: value for name, value in result.metrics.items()
            }
            await self._event(
                context,
                events,
                "tool_finished",
                {
                    "step_id": step.id,
                    "tool_name": step.tool_name,
                    "artifact_count": len(result.artifacts),
                    "metrics": event_metrics,
                },
            )
        self._check_cancelled(context)
        quality_score = await self._model.evaluate(combined)
        self._check_cancelled(context)
        if (
            isinstance(quality_score, bool)
            or not isinstance(quality_score, float)
            or not math.isfinite(quality_score)
            or not 0.0 <= quality_score <= 1.0
        ):
            raise AgentOutputError("model evaluation returned an invalid quality score")
        await self._event(
            context,
            events,
            "evaluation",
            {"quality_score": quality_score},
        )
        await self._event(
            context,
            events,
            "completion",
            {
                "artifact_names": [artifact.name for artifact in combined.artifacts],
                "demo": True,
            },
        )
        return AgentRunResult(
            plan=plan,
            quality_score=quality_score,
            artifacts=combined.artifacts,
            events=events,
            summary={
                "demo": True,
                "notice": "Deterministic mock output; no scientific processing performed.",
            },
        )

    def _validate_plan(self, raw_plan: Any) -> AgentPlan:
        try:
            candidate = raw_plan.model_dump() if isinstance(raw_plan, AgentPlan) else raw_plan
            plan = AgentPlan.model_validate(candidate, strict=True)
        except ValidationError as exc:
            raise AgentGuardrailError("invalid agent plan") from exc
        if len(plan.steps) > MAX_STEPS:
            raise AgentGuardrailError(f"agent plan exceeds {MAX_STEPS} steps")
        if plan.max_iterations > MAX_ITERATIONS:
            raise AgentGuardrailError(f"agent plan exceeds {MAX_ITERATIONS} iterations")
        return plan

    def _prepare_steps(
        self,
        plan: AgentPlan,
    ) -> list[tuple[Any, Tool, BaseModel]]:
        prepared: list[tuple[Any, Tool, BaseModel]] = []
        for step in plan.steps:
            tool = self._registry.resolve(step.tool_name, step.tool_version)
            try:
                arguments = tool.input_model.model_validate(step.arguments, strict=True)
            except ValidationError as exc:
                raise InvalidToolArgumentsError(
                    f"invalid arguments for {step.tool_name}@{step.tool_version}"
                ) from exc
            prepared.append((step, tool, arguments))
        return prepared

    def _validate_tool_result(self, raw_result: ToolResult) -> ToolResult:
        try:
            return ToolResult.model_validate(raw_result.model_dump(), strict=True)
        except (AttributeError, ValidationError) as exc:
            raise AgentOutputError("tool returned an invalid result") from exc

    def _verify_artifacts(
        self,
        result: ToolResult,
        artifact_names: set[str],
    ) -> None:
        if len(artifact_names) + len(result.artifacts) > MAX_ARTIFACT_COUNT:
            raise AgentOutputError("agent produced too many artifacts")
        for claimed in result.artifacts:
            if claimed.name in artifact_names:
                raise AgentOutputError("agent produced duplicate artifact names")
            try:
                actual = self._artifact_store.describe(claimed.name)
            except (FileNotFoundError, OSError, ValueError) as exc:
                raise AgentOutputError("tool claimed an invalid or missing artifact") from exc
            if actual != claimed:
                raise AgentOutputError("tool artifact claim does not match stored bytes")
            artifact_names.add(claimed.name)

    def _serialized_json_size(self, value: dict[str, JsonValue]) -> int:
        return len(
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    def _validate_context_paths(self, context: TaskContext) -> None:
        try:
            task_dir = context.task_dir.resolve(strict=True)
            source_path = context.source_path.resolve(strict=True)
            if not self._artifact_store.matches_root(context.task_dir):
                raise ValueError
            source_path.relative_to(task_dir)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise ArtifactPathError("task context path escapes task directory") from exc

    def _check_cancelled(self, context: TaskContext) -> None:
        if context.cancellation_check():
            raise AgentCancelledError()

    async def _delay_checkpoint(self, context: TaskContext) -> None:
        self._check_cancelled(context)
        remaining = self._step_delay_seconds
        while remaining > 0:
            interval = min(remaining, 0.05)
            await asyncio.sleep(interval)
            remaining -= interval
            self._check_cancelled(context)

    async def _event(
        self,
        context: TaskContext,
        events: list[AgentEvent],
        event_type: str,
        payload: dict[str, JsonValue],
    ) -> None:
        sequence = len(events) + 1
        event = AgentEvent(
            sequence=sequence,
            event_type=event_type,  # type: ignore[arg-type]
            payload=payload,
            timestamp=self._occurrence_timestamp(context, sequence),
        )
        events.append(event)
        if self._event_sink is not None:
            result = self._event_sink(event)
            if inspect.isawaitable(result):
                await result

    def _occurrence_timestamp(self, context: TaskContext, sequence: int) -> datetime:
        timestamp = self._occurrence_timestamp_provider(context, sequence)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise AgentOutputError("occurrence timestamp must be timezone-aware")
        return timestamp.astimezone(UTC)


def utc_occurrence_timestamp(_context: TaskContext, _sequence: int) -> datetime:
    """Return the real UTC time at which an Agent event occurs."""
    return datetime.now(UTC)
