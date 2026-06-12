from collections.abc import Callable
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


class AgentGuardrailError(ValueError):
    pass


class InvalidToolArgumentsError(AgentGuardrailError):
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
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._model = model
        self._registry = registry
        self._artifact_store = artifact_store
        self._clock = clock

    async def run(self, context: TaskContext) -> AgentRunResult:
        self._validate_context_paths(context)
        raw_plan = await self._model.plan(context)
        plan = self._validate_plan(raw_plan)
        prepared = self._prepare_steps(plan)
        events: list[AgentEvent] = []
        self._event(
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
            self._check_cancelled(context)
            self._event(
                events,
                "tool_started",
                {
                    "step_id": step.id,
                    "tool_name": step.tool_name,
                    "tool_version": step.tool_version,
                },
            )
            result = await tool.execute(context, arguments)
            self._check_cancelled(context)
            combined.observations[step.id] = result.observations
            combined.artifacts.extend(result.artifacts)
            combined.metrics.update(result.metrics)
            event_metrics: dict[str, JsonValue] = {
                name: value for name, value in result.metrics.items()
            }
            self._event(
                events,
                "tool_finished",
                {
                    "step_id": step.id,
                    "tool_name": step.tool_name,
                    "artifact_count": len(result.artifacts),
                    "metrics": event_metrics,
                },
            )
        quality_score = await self._model.evaluate(combined)
        self._event(events, "evaluation", {"quality_score": quality_score})
        self._event(
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
            plan = AgentPlan.model_validate(raw_plan)
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
                arguments = tool.input_model.model_validate(step.arguments)
            except ValidationError as exc:
                raise InvalidToolArgumentsError(
                    f"invalid arguments for {step.tool_name}@{step.tool_version}"
                ) from exc
            prepared.append((step, tool, arguments))
        return prepared

    def _validate_context_paths(self, context: TaskContext) -> None:
        try:
            task_dir = context.task_dir.resolve(strict=True)
            source_path = context.source_path.resolve(strict=True)
            if task_dir != self._artifact_store.root:
                raise ValueError
            source_path.relative_to(task_dir)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise ArtifactPathError("task context path escapes task directory") from exc

    def _check_cancelled(self, context: TaskContext) -> None:
        if context.cancellation_check():
            raise AgentCancelledError()

    def _event(
        self,
        events: list[AgentEvent],
        event_type: str,
        payload: dict[str, JsonValue],
    ) -> None:
        events.append(
            AgentEvent(
                sequence=len(events) + 1,
                event_type=event_type,  # type: ignore[arg-type]
                payload=payload,
                timestamp=self._clock(),
            )
        )
