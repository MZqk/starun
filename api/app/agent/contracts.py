from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import ProcessingStyle, TaskType
from app.fits.schemas import FitsInspection

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]


class TaskContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    task_id: str
    task_type: TaskType
    style: ProcessingStyle | None
    task_dir: Path
    source_path: Path
    fits_inspection: FitsInspection | None = None
    basic_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    cancellation_check: Callable[[], bool]


class AgentStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tool_name: str
    tool_version: str
    arguments: dict[str, JsonValue]


class AgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1"]
    steps: list[AgentStep]
    max_iterations: int = Field(ge=1, le=2)


class ArtifactManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    media_type: str
    size: int = Field(ge=0)
    sha256: str
    demo: Literal[True] = True


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: dict[str, JsonValue] = Field(default_factory=dict)
    artifacts: list[ArtifactManifestEntry] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

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
