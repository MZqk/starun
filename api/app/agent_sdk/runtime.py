from pathlib import Path
from typing import cast
import logging
import os

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
_DEFAULT_LOG_TEXT_LIMIT = 64 * 1024
_LOG_LINE_CHUNK_SIZE = 2000


def _debug_log_text_limit() -> int:
    configured = os.getenv("STARUN_DEBUG_LOG_TEXT_LIMIT")
    if configured is None:
        return _DEFAULT_LOG_TEXT_LIMIT
    try:
        return max(0, int(configured))
    except ValueError:
        return _DEFAULT_LOG_TEXT_LIMIT


def _decode_log_stream(value: bytes) -> tuple[str, bool]:
    text = value.decode("utf-8", errors="replace").strip()
    limit = _debug_log_text_limit()
    if len(text) <= limit:
        return text, False
    return text[:limit], True


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
        logger.debug(
            "Calling OpenAI Agents Runner: task_id=%s agent=%s skill=%s max_turns=%s input_chars=%s",
            spec.task_id,
            spec.agent_name,
            spec.skill_name,
            spec.max_turns,
            len(spec.input_text),
        )
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
        logger.debug(
            "OpenAI Agents Runner completed: task_id=%s agent=%s skill=%s result_type=%s",
            spec.task_id,
            spec.agent_name,
            spec.skill_name,
            type(result).__name__,
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
        "Direct skill entrypoint finished: task_id=%s skill=%s exit_code=%s stdout_bytes=%s stderr_bytes=%s",
        spec.task_id,
        spec.skill_name,
        result.exit_code,
        len(result.stdout),
        len(result.stderr),
    )
    _log_exec_stream(spec, "stdout", result.stdout)
    _log_exec_stream(spec, "stderr", result.stderr)


def _log_exec_stream(spec: AgentSdkRunSpec, stream_name: str, value: bytes) -> None:
    text, truncated = _decode_log_stream(value)
    if not text:
        logger.debug(
            "Direct skill %s is empty: task_id=%s skill=%s",
            stream_name,
            spec.task_id,
            spec.skill_name,
        )
        return
    logger.debug(
        "Direct skill %s captured: task_id=%s skill=%s chars=%s truncated=%s",
        stream_name,
        spec.task_id,
        spec.skill_name,
        len(text),
        truncated,
    )
    for line_number, line in enumerate(text.splitlines(), start=1):
        if len(line) <= _LOG_LINE_CHUNK_SIZE:
            logger.debug(
                "Direct skill %s: task_id=%s skill=%s line=%s text=%s",
                stream_name,
                spec.task_id,
                spec.skill_name,
                line_number,
                line,
            )
            continue
        for chunk_number, start in enumerate(range(0, len(line), _LOG_LINE_CHUNK_SIZE), start=1):
            logger.debug(
                "Direct skill %s: task_id=%s skill=%s line=%s chunk=%s text=%s",
                stream_name,
                spec.task_id,
                spec.skill_name,
                line_number,
                chunk_number,
                line[start : start + _LOG_LINE_CHUNK_SIZE],
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
