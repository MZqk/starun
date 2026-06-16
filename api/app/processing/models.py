from pydantic import BaseModel, ConfigDict, Field, field_validator

ARTWORK_DISCLAIMER = (
    "AI 自动出图为基于原始预览图生成的艺术增强图，不是可用于测光、科研或严格真实性"
    "验证的线性后期结果。"
)


class ArtDirection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_summary: str = Field(min_length=1, max_length=240)
    visible_subject: str = Field(min_length=1, max_length=160)
    quality_notes: list[str] = Field(min_length=1, max_length=8)
    generation_prompt: str = Field(min_length=20, max_length=1600)
    negative_prompt: str = Field(min_length=1, max_length=600)
    edit_intensity: str = Field(pattern="^(low|medium|high)$")
    risk_notes: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("edit_intensity", mode="before")
    @classmethod
    def normalize_edit_intensity(cls, value: object) -> object:
        if value == "balanced":
            return "medium"
        return value


class GeneratedArtwork(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    data: bytes
    media_type: str = Field(pattern="^image/(png|jpeg)$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    provider_request_id: str | None = None
    revised_prompt: str | None = None
    source_url_host: str | None = None


class ProcessingState:
    reference_name: str | None = None
    reference_width: int | None = None
    reference_height: int | None = None
    reference_png: bytes | None = None
    direction: ArtDirection | None = None
    generated: GeneratedArtwork | None = None
    generated_name: str | None = None
