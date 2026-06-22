import importlib.util
import json
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from agents.sandbox import Dir, LocalFile, Manifest
from agents.sandbox.capabilities import Shell, Skills
from agents.sandbox.capabilities.shell import ShellToolSet
from agents.sandbox.capabilities.tools.shell_tool import ExecCommandArgs, ExecCommandTool
from agents.sandbox.entries import File, LocalDir
from agents.sandbox.manifest import Environment
from agents.sandbox.types import Permissions
from agents.sandbox.workspace_paths import SandboxPathGrant
from pydantic import TypeAdapter

from app.agent_sdk.contracts import (
    AnalysisSkillResult,
    ProcessingSkillResult,
    SkillFailureResult,
    SkillRequest,
)
from app.agent_sdk.errors import AgentNotConfiguredError, SkillExecutionError
from app.fits.schemas import FitsInspection


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    path: Path


REQUIRED_SKILL_RUNTIME_MODULES = (
    "numpy",
    "astropy",
    "scipy",
    "PIL",
    "tifffile",
    "xisf",
)


class StarunExecCommandTool(ExecCommandTool):
    async def run(self, args: ExecCommandArgs) -> str:
        return await super().run(args.model_copy(update={"tty": True}))


def _configure_shell_tools(toolset: ShellToolSet) -> None:
    toolset.exec_command = StarunExecCommandTool(session=toolset.exec_command.session)


def build_task_manifest(
    *,
    source_path: Path,
    inspection: FitsInspection,
    request: SkillRequest,
) -> Manifest:
    if not source_path.is_file():
        raise AgentNotConfiguredError("Task source file is missing.")
    missing_dependencies = [
        module
        for module in REQUIRED_SKILL_RUNTIME_MODULES
        if importlib.util.find_spec(module) is None
    ]
    if missing_dependencies:
        raise SkillExecutionError(
            "Starun skill runtime dependencies are missing: "
            + ", ".join(missing_dependencies),
            code="runtime_dependency_missing",
        )
    workspace_id = sha256(request.task_id.encode("utf-8")).hexdigest()
    workspace_root = f"/tmp/starun-sandbox/{workspace_id}"
    runtime_python = str(Path(sys.executable).absolute())
    python_wrapper = (
        "#!/bin/sh\n"
        "unset VIRTUAL_ENV PYTHONHOME PYTHONPATH __PYVENV_LAUNCHER__ "
        "STARUN_SKILL_PYTHON\n"
        f"exec {runtime_python!r} \"$@\"\n"
    ).encode("utf-8")
    executable_permissions = Permissions(owner=0o7, group=0o5, other=0o5)
    runtime_roots = {
        str(Path(sys.prefix).resolve()),
        str(Path(sys.base_prefix).resolve()),
    }
    result_type = (
        AnalysisSkillResult | SkillFailureResult
        if request.task_type.value == "analysis"
        else ProcessingSkillResult | SkillFailureResult
    )
    result_schema = TypeAdapter(result_type).json_schema()
    return Manifest(
        root=workspace_root,
        environment=Environment(
            value={
                "STARUN_SKILL_SANDBOX": "1",
                "PATH": f"{workspace_root}/bin:/usr/local/bin:/usr/bin:/bin",
                "VIRTUAL_ENV": "",
                "PYTHONHOME": "",
                "PYTHONPATH": "",
                "__PYVENV_LAUNCHER__": "",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            }
        ),
        extra_path_grants=tuple(
            SandboxPathGrant(
                path=path,
                read_only=True,
                description="Preinstalled Python runtime used by Starun skills.",
            )
            for path in sorted(runtime_roots)
        ),
        entries={
            "bin": Dir(
                children={
                    "python": File(
                        content=python_wrapper,
                        permissions=executable_permissions,
                    ),
                    "python3": File(
                        content=python_wrapper,
                        permissions=executable_permissions,
                    ),
                }
            ),
            "input": Dir(
                children={
                    "source.fits": LocalFile(src=source_path),
                    "inspection.json": File(
                        content=json.dumps(
                            inspection.model_dump(mode="json"),
                            ensure_ascii=True,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ),
                    "request.json": File(
                        content=request.model_dump_json().encode("utf-8")
                    ),
                    "result-schema.json": File(
                        content=json.dumps(
                            result_schema,
                            ensure_ascii=True,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ),
                }
            ),
            "output": Dir(children={}),
        }
    )


def build_skill_capabilities(skill: SkillDefinition) -> list[Shell | Skills]:
    if not skill.path.is_dir():
        raise AgentNotConfiguredError(
            f"Configured skill directory is missing: {skill.name}"
        )
    return [
        Shell(configure_tools=_configure_shell_tools),
        Skills(
            from_=Dir(
                children={
                    skill.name: LocalDir(src=skill.path),
                }
            )
        ),
    ]
