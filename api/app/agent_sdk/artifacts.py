from collections.abc import Iterable
from typing import Protocol

from app.agent_sdk.contracts import SkillArtifactClaim
from app.agent_sdk.errors import SkillOutputError
from app.artifacts.contracts import ArtifactManifestEntry, media_type_for_name
from app.artifacts.store import (
    ArtifactPathError,
    ArtifactSizeError,
    ArtifactStore,
    UnsupportedArtifactError,
)


class SandboxOutputReader(Protocol):
    async def read_bytes(self, path: str) -> bytes: ...


async def publish_claimed_artifacts(
    session: SandboxOutputReader,
    store: ArtifactStore,
    claims: Iterable[SkillArtifactClaim],
) -> list[ArtifactManifestEntry]:
    published: list[ArtifactManifestEntry] = []
    seen: set[str] = set()
    for claim in claims:
        if claim.name in seen:
            raise SkillOutputError("Skill declared a duplicate artifact.")
        seen.add(claim.name)
        if media_type_for_name(claim.name) != claim.media_type:
            raise SkillOutputError("Skill artifact media type does not match its name.")
        try:
            data = await session.read_bytes(f"output/{claim.name}")
            published.append(store.write_bytes(claim.name, data))
        except FileNotFoundError as exc:
            raise SkillOutputError("A declared skill artifact is missing.") from exc
        except (ArtifactPathError, ArtifactSizeError, UnsupportedArtifactError) as exc:
            raise SkillOutputError("A declared skill artifact is invalid.") from exc
    return published
