from pathlib import Path

from agents import RunConfig, Runner
from agents.run_config import SandboxRunConfig
from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient
from agents.sandbox.session.sandbox_session import SandboxSession

from app.agent_sdk.runtime_types import AgentSdkRunSpec, EventEmitter


class OpenAiSandboxRuntime:
    def __init__(self, spec: AgentSdkRunSpec) -> None:
        self._spec = spec
        self._client = UnixLocalSandboxClient()
        self._session: SandboxSession | None = None

    async def run(self, spec: AgentSdkRunSpec, emit: EventEmitter) -> object:
        self._session = await self._client.create(manifest=spec.manifest)
        await emit("run_started", {"agent": spec.agent_name})
        await emit(
            "tool_started",
            {"step_id": "01", "tool_name": spec.skill_name},
        )
        result = await Runner.run(
            spec.agent,
            input=spec.input_text,
            max_turns=spec.max_turns,
            run_config=RunConfig(
                sandbox=SandboxRunConfig(session=self._session),
                tracing_disabled=True,
                trace_include_sensitive_data=False,
                workflow_name=spec.agent_name,
                group_id=spec.task_id,
            ),
        )
        await emit(
            "tool_finished",
            {"step_id": "01", "tool_name": spec.skill_name},
        )
        return result

    async def read_bytes(self, path: str) -> bytes:
        if self._session is None:
            raise RuntimeError("sandbox session is not active")
        stream = await self._session.read(Path(path))
        data = stream.read()
        if not isinstance(data, bytes):
            raise TypeError("sandbox file read returned non-bytes content")
        return data

    async def close(self) -> None:
        if self._session is not None:
            await self._session.aclose()

    async def delete(self) -> None:
        if self._session is not None:
            await self._client.delete(self._session)
            self._session = None
