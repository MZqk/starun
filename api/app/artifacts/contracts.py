import re
from pathlib import PurePath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
type MediaType = Literal["application/json", "image/jpeg", "image/png", "image/tiff"]
MEDIA_TYPES: dict[str, MediaType] = {
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

type JsonPrimitive = str | int | float | bool | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SAFE_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_artifact_name(name: str) -> str:
    path = PurePath(name)
    if (
        not name
        or name in {".", ".."}
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or path.name != name
        or SAFE_ARTIFACT_NAME.fullmatch(name) is None
    ):
        raise ValueError("artifact name must be a flat safe basename")
    return name


def media_type_for_name(name: str) -> MediaType:
    validate_artifact_name(name)
    suffix = PurePath(name).suffix
    try:
        return MEDIA_TYPES[suffix]
    except KeyError as exc:
        raise ValueError(f"unsupported artifact media type: {suffix}") from exc


class ArtifactManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    media_type: MediaType
    size: int = Field(ge=0, le=MAX_ARTIFACT_BYTES, strict=True)
    sha256: Sha256
    demo: bool = False

    @model_validator(mode="after")
    def validate_name_and_media_type(self) -> "ArtifactManifestEntry":
        expected_media_type = media_type_for_name(self.name)
        if self.media_type != expected_media_type:
            raise ValueError("artifact media type does not match its name")
        return self
