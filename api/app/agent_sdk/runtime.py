from pathlib import Path

from agents import RunConfig, Runner
from agents.sandbox import Dir, Manifest
from agents.run_config import SandboxRunConfig
from agents.sandbox.entries import LocalDir
from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient
from agents.sandbox.session.sandbox_session import SandboxSession

from app.agent_sdk.runtime_types import AgentSdkRunSpec, EventEmitter


def _manifest_with_skill(spec: AgentSdkRunSpec) -> Manifest:
    if spec.skill_path is None:
        return spec.manifest
    entries = dict(spec.manifest.entries)
    entries[".agents"] = Dir(
        children={
            spec.skill_name: LocalDir(src=spec.skill_path),
        }
    )
    return spec.manifest.model_copy(update={"entries": entries})


class OpenAiSandboxRuntime:
    def __init__(self, spec: AgentSdkRunSpec) -> None:
        self._spec = spec
        self._client = UnixLocalSandboxClient()
        self._session: SandboxSession | None = None

    async def run(self, spec: AgentSdkRunSpec, emit: EventEmitter) -> object:
        if spec.style is not None and spec.style.value == "artistic":
            raise RuntimeError("artistic processing does not use the skill sandbox runtime")
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


class DirectProcessingSkillRuntime:
    def __init__(self, spec: AgentSdkRunSpec) -> None:
        self._spec = spec
        self._client = UnixLocalSandboxClient()
        self._session: SandboxSession | None = None

    async def run(self, spec: AgentSdkRunSpec, emit: EventEmitter) -> object:
        if spec.skill_name != "deep-sky-processor":
            raise RuntimeError("direct processing runtime only supports deep-sky-processor")
        self._session = await self._client.create(manifest=_manifest_with_skill(spec))
        await emit("run_started", {"agent": spec.agent_name, "mode": "direct_skill_entrypoint"})
        await emit("tool_started", {"step_id": "01", "tool_name": spec.skill_name})
        command = _processing_entrypoint_command(spec)
        result = await self._session.exec(command, shell=True)
        await emit(
            "tool_finished",
            {
                "step_id": "01",
                "tool_name": spec.skill_name,
                "exit_code": result.exit_code,
            },
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


def _processing_entrypoint_command(spec: AgentSdkRunSpec) -> str:
    source = "source.xisf" if spec.source_path.suffix.lower() == ".xisf" else "source.fits"
    return (
        "cd .agents/deep-sky-processor && "
        f"python scripts/run_starun_processing.py --source ../../input/{source} "
        "--output-dir ../../output "
        "--result ../../output/processing-result.json "
        "--request-json ../../input/request.json "
        "--inspection-json ../../input/inspection.json "
        "--schema-json ../../input/result-schema.json"
    )
