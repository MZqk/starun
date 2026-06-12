from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.artifacts.contracts import (
    ArtifactManifestEntry as ArtifactManifestEntry,
    JsonValue as JsonValue,
)
from app.db.models import ProcessingStyle, TaskType
from app.fits.schemas import FitsInspection


class TaskContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", strict=True)

    task_id: str
    task_type: TaskType
    style: ProcessingStyle | None
    task_dir: Path
    source_path: Path
    fits_inspection: FitsInspection | None = None
    basic_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    cancellation_check: Callable[[], bool]


class AgentStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    tool_name: str
    tool_version: str
    arguments: dict[str, JsonValue]


class AgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal["1"]
    steps: list[AgentStep]
    max_iterations: int = Field(ge=1, le=2, strict=True)

    @model_validator(mode="after")
    def validate_unique_step_ids(self) -> "AgentPlan":
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("agent step ids must be unique")
        return self

class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    observations: dict[str, JsonValue] = Field(default_factory=dict)
    artifacts: list[ArtifactManifestEntry] = Field(default_factory=list)
    metrics: dict[
        str,
        Annotated[float, Field(allow_inf_nan=False, strict=True)],
    ] = Field(default_factory=dict)


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sequence: int = Field(ge=1)
    event_type: Literal[
        "plan",
        "tool_started",
        "tool_finished",
        "evaluation",
        "completion",
    ]
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    timestamp: datetime | None = None


class AgentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    plan: AgentPlan
    quality_score: float = Field(ge=0.0, le=1.0)
    artifacts: list[ArtifactManifestEntry]
    events: list[AgentEvent]
    summary: dict[str, JsonValue] = Field(default_factory=dict)


class Tool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def input_model(self) -> type[BaseModel]: ...

    async def execute(
        self,
        context: TaskContext,
        arguments: BaseModel,
    ) -> ToolResult: ...


class ModelAdapter(Protocol):
    async def plan(self, context: TaskContext) -> AgentPlan: ...

    async def evaluate(self, observation: ToolResult) -> float: ...
