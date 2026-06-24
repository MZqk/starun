from pathlib import Path
from typing import cast
import logging

from agents import Agent, RunConfig, Runner
from agents.sandbox import Dir, Manifest
from agents.sandbox.errors import WorkspaceReadNotFoundError
from agents.run_config import SandboxRunConfig
from agents.sandbox.entries import LocalDir
from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient
from agents.sandbox.session.sandbox_session import SandboxSession
from agents.sandbox.types import ExecResult

from app.agent_sdk.runtime_types import AgentSdkRunSpec, EventEmitter

logger = logging.getLogger(__name__)
_LOG_TEXT_LIMIT = 4000


def _log_text(value: bytes) -> str:
    text = value.decode("utf-8", errors="replace").strip()
    if len(text) <= _LOG_TEXT_LIMIT:
        return text
    return text[:_LOG_TEXT_LIMIT] + "... <truncated>"


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
        logger.debug(
            "Starting OpenAI sandbox runtime: task_id=%s agent=%s skill=%s root=%s result_path=%s",
            spec.task_id,
            spec.agent_name,
            spec.skill_name,
            spec.manifest.root,
            spec.result_path,
        )
        await emit("run_started", {"agent": spec.agent_name})
        await emit(
            "tool_started",
            {"step_id": "01", "tool_name": spec.skill_name},
        )
        if spec.agent is None:
            raise RuntimeError("OpenAI sandbox runtime requires an agent")
        result = await Runner.run(
            cast(Agent[None], spec.agent),
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
        manifest = _manifest_with_skill(spec)
        self._session = await self._client.create(manifest=manifest)
        logger.debug(
            "Starting direct processing runtime: task_id=%s skill=%s root=%s skill_path=%s result_path=%s",
            spec.task_id,
            spec.skill_name,
            manifest.root,
            spec.skill_path,
            spec.result_path,
        )
        await self._session.start()
        await emit("run_started", {"agent": spec.agent_name, "mode": "direct_skill_entrypoint"})
        await emit("tool_started", {"step_id": "01", "tool_name": spec.skill_name})
        command = _processing_entrypoint_command(spec)
        logger.debug("Executing direct processing entrypoint: task_id=%s command=%s", spec.task_id, command)
        result = await self._session.exec(command, shell=True)
        _log_exec_result(spec, result)
        await _raise_if_failed_without_result(self._session, spec, result)
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


class DirectAnalysisSkillRuntime:
    def __init__(self, spec: AgentSdkRunSpec) -> None:
        self._spec = spec
        self._client = UnixLocalSandboxClient()
        self._session: SandboxSession | None = None

    async def run(self, spec: AgentSdkRunSpec, emit: EventEmitter) -> object:
        if spec.skill_name != "deep-sky-advisor":
            raise RuntimeError("direct analysis runtime only supports deep-sky-advisor")
        manifest = _manifest_with_skill(spec)
        self._session = await self._client.create(manifest=manifest)
        logger.debug(
            "Starting direct analysis runtime: task_id=%s skill=%s root=%s skill_path=%s result_path=%s",
            spec.task_id,
            spec.skill_name,
            manifest.root,
            spec.skill_path,
            spec.result_path,
        )
        await self._session.start()
        await emit("run_started", {"agent": spec.agent_name, "mode": "direct_skill_entrypoint"})
        await emit("tool_started", {"step_id": "01", "tool_name": spec.skill_name})
        command = _analysis_entrypoint_command(spec)
        logger.debug("Executing direct analysis entrypoint: task_id=%s command=%s", spec.task_id, command)
        result = await self._session.exec(command, shell=True)
        _log_exec_result(spec, result)
        await _raise_if_failed_without_result(self._session, spec, result)
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


def _analysis_entrypoint_command(spec: AgentSdkRunSpec) -> str:
    source = "source.xisf" if spec.source_path.suffix.lower() == ".xisf" else "source.fits"
    return (
        "cd .agents/deep-sky-advisor && "
        f"python scripts/run_starun_analysis.py --source ../../input/{source} "
        "--output-dir ../../output "
        "--result ../../output/analysis-result.json "
        "--request-json ../../input/request.json "
        "--schema-json ../../input/result-schema.json"
    )


async def _raise_if_failed_without_result(
    session: SandboxSession,
    spec: AgentSdkRunSpec,
    result: ExecResult,
) -> None:
    if result.exit_code == 0:
        return
    try:
        await session.read(Path(spec.result_path))
        return
    except WorkspaceReadNotFoundError:
        pass
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    detail = stderr or stdout or f"exit code {result.exit_code}"
    raise RuntimeError(f"{spec.skill_name} entrypoint failed before writing result: {detail}")


def _log_exec_result(spec: AgentSdkRunSpec, result: ExecResult) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return
    logger.debug(
        "Direct skill entrypoint finished: task_id=%s skill=%s exit_code=%s stdout=%r stderr=%r",
        spec.task_id,
        spec.skill_name,
        result.exit_code,
        _log_text(result.stdout),
        _log_text(result.stderr),
    )


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
