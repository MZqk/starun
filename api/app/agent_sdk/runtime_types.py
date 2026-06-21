from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agents import Agent
from agents.sandbox import Manifest

from app.db.models import ProcessingStyle, TaskType
from app.fits.schemas import FitsInspection

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
    agent: Agent[None]
    manifest: Manifest
    input_text: str
    source_path: Path
    inspection: FitsInspection


class AgentSdkRuntime(Protocol):
    async def run(self, spec: AgentSdkRunSpec, emit: EventEmitter) -> object: ...

    async def read_bytes(self, path: str) -> bytes: ...

    async def close(self) -> None: ...

    async def delete(self) -> None: ...
