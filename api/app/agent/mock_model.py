import hashlib
from datetime import UTC, datetime, timedelta

from app.agent.contracts import AgentPlan, AgentStep, JsonValue, TaskContext, ToolResult
from app.db.models import ProcessingStyle


class DeterministicMockModel:
    async def plan(self, context: TaskContext) -> AgentPlan:
        style = context.style or ProcessingStyle.BALANCED
        seed = _seed(context.task_id, style)
        profiles = {
            ProcessingStyle.REALISTIC: (0.18, 0.30, 0.12, 0.95),
            ProcessingStyle.BALANCED: (0.32, 0.45, 0.24, 1.00),
            ProcessingStyle.ARTISTIC: (0.48, 0.58, 0.38, 1.12),
        }
        denoise, sharpen, saturation, stretch = profiles[style]
        jitter = (seed % 17) / 1000
        steps = [
            _step("01", "mock.inspect", {}),
            _step("02", "mock.stretch", {"strength": stretch + jitter}),
            _step("03", "mock.denoise", {"strength": denoise + jitter}),
            _step("04", "mock.sharpen", {"strength": sharpen + jitter}),
            _step("05", "mock.color", {"saturation": saturation + jitter}),
            _step("06", "mock.evaluate", {"seed": seed}),
            _step(
                "07",
                "mock.export",
                {
                    "seed": seed,
                    "style": style.value,
                },
            ),
        ]
        return AgentPlan(version="1", steps=steps, max_iterations=1 + seed % 2)

    async def evaluate(self, observation: ToolResult) -> float:
        score = observation.metrics.get("mock_quality")
        if score is None:
            raise ValueError("mock evaluation metric missing")
        return score


def _seed(task_id: str, style: ProcessingStyle) -> int:
    digest = hashlib.sha256(f"{task_id}{style.value}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def deterministic_mock_occurrence_timestamp(
    context: TaskContext,
    sequence: int,
) -> datetime:
    style = context.style or ProcessingStyle.BALANCED
    seconds = _seed(context.task_id, style) % (50 * 365 * 24 * 60 * 60)
    base = datetime(2000, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)
    return base + timedelta(microseconds=sequence)


def _step(identifier: str, name: str, arguments: dict[str, JsonValue]) -> AgentStep:
    return AgentStep(
        id=identifier,
        tool_name=name,
        tool_version="v1",
        arguments=arguments,
    )
