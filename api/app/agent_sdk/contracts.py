from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.analysis.models import ProfessionalAnalysis
from app.artifacts.contracts import (
    ArtifactManifestEntry,
    JsonValue,
    MediaType,
    validate_artifact_name,
)
from app.db.models import ProcessingStyle, TaskType


class SkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["starun.skill-request/v1"] = "starun.skill-request/v1"
    task_id: str = Field(min_length=1, max_length=128)
    task_type: TaskType
    locale: Literal["zh-CN"] = "zh-CN"
    source_path: Literal["input/source.fits", "input/source.xisf"] = "input/source.fits"
    inspection_path: Literal["input/inspection.json"] = "input/inspection.json"
    output_dir: Literal["output"] = "output"
    style: ProcessingStyle | None = None

    @model_validator(mode="after")
    def validate_style(self) -> "SkillRequest":
        if self.task_type is TaskType.PROCESSING and self.style is None:
            raise ValueError("processing request requires style")
        if self.task_type is TaskType.ANALYSIS and self.style is not None:
            raise ValueError("analysis request cannot include style")
        return self


class SkillArtifactClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    media_type: MediaType

    @model_validator(mode="after")
    def validate_name(self) -> "SkillArtifactClaim":
        validate_artifact_name(self.name)
        if PurePosixPath(self.name).name != self.name:
            raise ValueError("artifact must be a flat output basename")
        return self


class AnalysisPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    artifact: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    lower_percentile_value: float
    upper_percentile_value: float


class AnalysisSkillResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["starun.skill-result/v1"]
    status: Literal["success"]
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=200)
    preview: AnalysisPreview
    analysis: ProfessionalAnalysis
    artifacts: list[SkillArtifactClaim] = Field(min_length=2, max_length=16)

    @model_validator(mode="after")
    def validate_artifact_references(self) -> "AnalysisSkillResult":
        names = {artifact.name for artifact in self.artifacts}
        if self.preview.artifact not in names:
            raise ValueError("preview artifact is not declared")
        if "analysis-report.json" not in names:
            raise ValueError("analysis report is not declared")
        return self


class ProcessingSkillResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["starun.skill-result/v1"]
    status: Literal["success"]
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=200)
    style: ProcessingStyle
    reference_artifact: str
    result_artifact: str
    target_summary: str = Field(min_length=1, max_length=240)
    visible_subject: str = Field(min_length=1, max_length=512)
    art_direction_summary: str = Field(min_length=1, max_length=1600)
    quality_score: float = Field(ge=0, le=1)
    result_width: int | None = Field(default=None, gt=0)
    result_height: int | None = Field(default=None, gt=0)
    provider_request_id: str | None = Field(default=None, max_length=200)
    pipeline_status: Literal[
        "success",
        "partial_success",
        "review_required",
        "failed",
    ]
    quality_gates: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=32)
    warnings: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=32)
    artifacts: list[SkillArtifactClaim] = Field(min_length=2, max_length=16)

    @model_validator(mode="after")
    def validate_artifact_references(self) -> "ProcessingSkillResult":
        names = {artifact.name for artifact in self.artifacts}
        if not {self.reference_artifact, self.result_artifact} <= names:
            raise ValueError("processing artifacts are not declared")
        if self.style is ProcessingStyle.BALANCED and "style-prompt.json" not in names:
            raise ValueError("balanced processing must declare style-prompt.json")

        allowed_extensions = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".fits", ".fit", ".fts"}
        for attr, val in [("reference_artifact", self.reference_artifact), ("result_artifact", self.result_artifact)]:
            suffix = PurePosixPath(val).suffix.lower()
            if suffix not in allowed_extensions:
                raise ValueError(
                    f"{attr} must be an image file (one of {', '.join(allowed_extensions)}), "
                    f"but got: '{val}'"
                )
        return self


class SkillFailureResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["starun.skill-result/v1"]
    status: Literal["failed"]
    error_code: Literal[
        "runtime_dependency_missing",
        "skill_command_failed",
        "skill_output_missing",
        "skill_output_invalid",
    ]
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool = False
    missing_dependencies: list[str] = Field(default_factory=list, max_length=32)


class PublishedSkillRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    artifacts: list[ArtifactManifestEntry]
    summary: dict[str, JsonValue]
    quality_score: float | None = None
    pipeline_status: Literal[
        "success",
        "partial_success",
        "review_required",
        "failed",
    ] = "success"
