from app.agent.contracts import AgentRunResult, TaskContext
from app.agent.mock_model import DeterministicMockModel
from app.agent.mock_tools import build_mock_tools
from app.agent.registry import ToolRegistry
from app.agent.runner import AgentRunner
from app.artifacts.store import ArtifactStore


def build_mock_runner(artifact_store: ArtifactStore) -> AgentRunner:
    return AgentRunner(
        model=DeterministicMockModel(),
        registry=ToolRegistry(build_mock_tools(artifact_store)),
        artifact_store=artifact_store,
    )


__all__ = [
    "AgentRunResult",
    "AgentRunner",
    "DeterministicMockModel",
    "TaskContext",
    "ToolRegistry",
    "build_mock_runner",
]
