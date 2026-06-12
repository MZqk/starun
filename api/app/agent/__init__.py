from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.runner import AgentRunner
    from app.artifacts.store import ArtifactStore


def build_mock_runner(artifact_store: "ArtifactStore") -> "AgentRunner":
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
    )


__all__ = ["build_mock_runner"]
