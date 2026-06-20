import json
from hashlib import sha256
from dataclasses import dataclass
from pathlib import Path

from agents.sandbox import Dir, LocalFile, Manifest
from agents.sandbox.capabilities import Shell, Skills
from agents.sandbox.entries import File, LocalDir

from app.agent_sdk.contracts import SkillRequest
from app.agent_sdk.errors import AgentNotConfiguredError
from app.fits.schemas import FitsInspection


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    path: Path


def build_task_manifest(
    *,
    source_path: Path,
    inspection: FitsInspection,
    request: SkillRequest,
) -> Manifest:
    if not source_path.is_file():
        raise AgentNotConfiguredError("Task source file is missing.")
    workspace_id = sha256(request.task_id.encode("utf-8")).hexdigest()
    return Manifest(
        root=f"/tmp/starun-sandbox/{workspace_id}",
        entries={
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
        Shell(),
        Skills(
            from_=Dir(
                children={
                    skill.name: LocalDir(src=skill.path),
                }
            )
        ),
    ]
