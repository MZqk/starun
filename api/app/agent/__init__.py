from importlib import import_module
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.contracts import AgentEvent, AgentRunResult, TaskContext
    from app.agent.mock_model import DeterministicMockModel
    from app.agent.registry import ToolRegistry
    from app.agent.runner import AgentRunner
    from app.artifacts.store import ArtifactStore

_LAZY_EXPORTS = {
    "AgentRunResult": ("app.agent.contracts", "AgentRunResult"),
    "AgentRunner": ("app.agent.runner", "AgentRunner"),
    "DeterministicMockModel": ("app.agent.mock_model", "DeterministicMockModel"),
    "TaskContext": ("app.agent.contracts", "TaskContext"),
    "ToolRegistry": ("app.agent.registry", "ToolRegistry"),
}


def __getattr__(name: str) -> object:
    try:
        module_name, attribute_name = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def build_mock_runner(
    artifact_store: "ArtifactStore",
    *,
    step_delay_seconds: float = 0,
    event_sink: "Callable[[AgentEvent], Awaitable[None] | None] | None" = None,
) -> "AgentRunner":
    from app.agent.mock_model import (
        DeterministicMockModel,
        deterministic_mock_occurrence_timestamp,
    )
    from app.agent.mock_tools import build_mock_tools
    from app.agent.registry import ToolRegistry
    from app.agent.runner import AgentRunner

    return AgentRunner(
        model=DeterministicMockModel(),
        registry=ToolRegistry(build_mock_tools(artifact_store)),
        artifact_store=artifact_store,
        occurrence_timestamp_provider=deterministic_mock_occurrence_timestamp,
        step_delay_seconds=step_delay_seconds,
        event_sink=event_sink,
    )


__all__ = [
    "AgentRunResult",
    "AgentRunner",
    "DeterministicMockModel",
    "TaskContext",
    "ToolRegistry",
    "build_mock_runner",
]
