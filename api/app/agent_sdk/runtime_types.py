from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from agents.sandbox import Manifest, SandboxAgent

from app.db.models import ProcessingStyle, TaskType

EventEmitter = Callable[[str, dict[str, object]], Awaitable[None]]


@dataclass(frozen=True)
class AgentSdkRunSpec:
    task_id: str
    task_type: TaskType
    style: ProcessingStyle | None
    agent_name: str
    skill_name: str
    result_path: str
    max_turns: int
    agent: SandboxAgent[None]
    manifest: Manifest
    input_text: str


class AgentSdkRuntime(Protocol):
    async def run(self, spec: AgentSdkRunSpec, emit: EventEmitter) -> object: ...

    async def read_bytes(self, path: str) -> bytes: ...

    async def close(self) -> None: ...

    async def delete(self) -> None: ...
