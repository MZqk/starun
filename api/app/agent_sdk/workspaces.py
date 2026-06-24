import importlib.util
import json
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from agents.sandbox import Dir, LocalFile, Manifest
from agents.sandbox.entries import File
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
        "unset PYTHONHOME __PYVENV_LAUNCHER__ STARUN_SKILL_PYTHON\n"
        "export VIRTUAL_ENV=\"/app/.venv\"\n"
        "export PYTHONPATH=\"/app/.venv/lib/python3.12/site-packages\"\n"
        f"exec {runtime_python!r} \"$@\"\n"
    ).encode("utf-8")
    executable_permissions = Permissions(owner=0o7, group=0o5, other=0o5)
    runtime_roots = {
        str(Path(sys.prefix).resolve()),
        str(Path(sys.base_prefix).resolve()),
    }
    import shutil
    starnet_dir = None
    starnet_bin = shutil.which("starnet2") or shutil.which("starnet++")
    if starnet_bin:
        starnet_dir = Path(starnet_bin).resolve().parent
    else:
        project_root = Path(__file__).resolve().parents[3]
        candidates = [
            project_root / "api" / "starnet2",
            project_root / "deep-sky-processor" / "scripts" / "StarNet2",
        ]
        for candidate in candidates:
            if (candidate / "starnet++").is_file() or (candidate / "starnet2").is_file():
                starnet_dir = candidate.resolve()
                break
    if starnet_dir:
        runtime_roots.add(str(starnet_dir))

    result_type = (
        AnalysisSkillResult | SkillFailureResult
        if request.task_type.value == "analysis"
        else ProcessingSkillResult | SkillFailureResult
    )
    result_schema = TypeAdapter(result_type).json_schema()
    host_tmps = set()
    if sys.platform == "darwin":
        import tempfile
        host_tmp = tempfile.gettempdir()
        resolved_host_tmp = str(Path(host_tmp).resolve())
        host_tmps.add(host_tmp)
        host_tmps.add(resolved_host_tmp)

    sandbox_path = f"{workspace_root}/bin:/usr/local/bin:/usr/bin:/bin"
    if starnet_dir:
        sandbox_path = f"{sandbox_path}:{starnet_dir}"

    return Manifest(
        root=workspace_root,
        environment=Environment(
            value={
                "STARUN_SKILL_SANDBOX": "1",
                "PATH": sandbox_path,
                "VIRTUAL_ENV": "/app/.venv",
                "PYTHONHOME": "",
                "PYTHONPATH": "/app/.venv/lib/python3.12/site-packages",
                "__PYVENV_LAUNCHER__": "",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "TMPDIR": "/tmp",
                "TEMP": "/tmp",
                "TMP": "/tmp",
            }
        ),
        extra_path_grants=tuple(
            SandboxPathGrant(
                path=path,
                read_only=True,
                description="Preinstalled Python runtime used by Starun skills.",
            )
            for path in sorted(runtime_roots)
        ) + tuple(
            SandboxPathGrant(
                path=path,
                read_only=False,
                description="Host temp directory for CoreML model compilation.",
            )
            for path in sorted(host_tmps)
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
                    Path(request.source_path).name: LocalFile(src=source_path),
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
