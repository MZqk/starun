import hashlib
import json
import os
import tempfile
from pathlib import Path

from app.agent.contracts import ArtifactManifestEntry, JsonValue


class ArtifactPathError(ValueError):
    pass


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ArtifactPathError("artifact root must be a directory")

    def safe_path(self, name: str) -> Path:
        relative = Path(name)
        if not name or relative.is_absolute() or ".." in relative.parts:
            raise ArtifactPathError("artifact path escapes task directory")
        candidate = self.root.joinpath(relative)
        try:
            candidate.parent.resolve(strict=True).relative_to(self.root)
            if candidate.exists() or candidate.is_symlink():
                candidate.resolve(strict=True).relative_to(self.root)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise ArtifactPathError("artifact path escapes task directory") from exc
        return candidate

    def write_bytes(self, name: str, data: bytes) -> ArtifactManifestEntry:
        path = self.safe_path(name)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        return self.describe(name)

    def write_json(
        self,
        name: str,
        value: dict[str, JsonValue],
    ) -> ArtifactManifestEntry:
        data = (
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        return self.write_bytes(name, data)

    def read_bytes(self, name: str) -> bytes:
        return self.safe_path(name).read_bytes()

    def describe(self, name: str) -> ArtifactManifestEntry:
        data = self.read_bytes(name)
        return ArtifactManifestEntry(
            name=name,
            media_type=_media_type(name),
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )


def _media_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    media_types = {
        ".json": "application/json",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }
    try:
        return media_types[suffix]
    except KeyError as exc:
        raise ValueError(f"unsupported artifact media type: {suffix}") from exc
