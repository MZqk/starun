from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ARTWORK_DISCLAIMER = (
    "AI 自动出图为基于原始预览图生成的艺术增强图，不是可用于测光、科研或严格真实性"
    "验证的线性后期结果。"
)

DIRECT_ARTISTIC_GENERATION_PROMPT = (
    "在严格参照输入图像的前提下，对同一张深空天文照片进行专业、克制的艺术化后期增强。"
    "增强重点为背景更干净、星点更受控、星云边界和已有弱结构更清晰、色彩更鲜明但不过度"
    "荧光化。只能基于原图已有信号做背景降噪、轻微星点控制、非线性拉伸、色彩校准、"
    "色调映射、局部对比度优化、亮度平衡、饱和度增强和轻微清晰度提升。"
)

DIRECT_ARTISTIC_NEGATIVE_PROMPT = (
    "重新创作、重绘、生成另一张同类天体图片、凭天体名称或图库想象结构、新增星体、删除星体、"
    "移动星点、替换星云、扩写尘埃暗带、重塑细丝纹理、改变视场、改变构图、改变裁切、改变方向、"
    "边框、文字、标题、签名、Logo、水印、AI生成标识、过饱和、塑料感、强烈光晕、科幻插画风格。"
)


def direct_artistic_direction() -> "ArtDirection":
    return ArtDirection(
        target_summary="基于输入参考图的深空天文照片艺术增强",
        visible_subject="以输入参考图中已经可见的星场、星云、尘埃、暗部结构和背景层次为唯一依据。",
        quality_notes=["艺术模式直接使用固定保真提示词，不经过出图规划阶段。"],
        generation_prompt=DIRECT_ARTISTIC_GENERATION_PROMPT,
        negative_prompt=DIRECT_ARTISTIC_NEGATIVE_PROMPT,
        edit_intensity="low",
        risk_notes=[
            "图片生成模型可能忽略参考图并重绘天体；提示词已强调必须保留原始视场和所有可见结构。"
        ],
    )


class ArtDirection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_summary: str = Field(min_length=1, max_length=240)
    visible_subject: str = Field(min_length=1, max_length=512)
    quality_notes: list[str] = Field(min_length=1, max_length=8)
    generation_prompt: str = Field(min_length=20, max_length=1600)
    negative_prompt: str = Field(min_length=1, max_length=600)
    edit_intensity: Literal["low", "medium", "high"]
    risk_notes: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("edit_intensity", mode="before")
    @classmethod
    def normalize_edit_intensity(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        aliases = {
            "low": "low",
            "light": "low",
            "mild": "low",
            "subtle": "low",
            "restrained": "low",
            "realistic": "low",
            "写实": "low",
            "低": "low",
            "轻微": "low",
            "克制": "low",
            "medium": "medium",
            "moderate": "medium",
            "balanced": "medium",
            "normal": "medium",
            "适中": "medium",
            "中等": "medium",
            "平衡": "medium",
            "high": "high",
            "strong": "high",
            "intense": "high",
            "artistic": "high",
            "高": "high",
            "强": "high",
            "艺术": "high",
        }
        if normalized in aliases:
            return aliases[normalized]
        return "medium"


class GeneratedArtwork(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    data: bytes
    media_type: str = Field(pattern="^image/(png|jpeg)$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    provider_width: int = Field(gt=0)
    provider_height: int = Field(gt=0)
    normalized_to_requested_size: bool
    provider_request_id: str | None = None
    revised_prompt: str | None = None
    source_url_host: str | None = None
    provider_request_controls: dict[str, object] = Field(default_factory=dict)


class ProcessingState:
    def __init__(self) -> None:
        self.reference_name: str | None = None
        self.reference_width: int | None = None
        self.reference_height: int | None = None
        self.reference_png: bytes | None = None
        self.direction: ArtDirection | None = None
        self.generated: GeneratedArtwork | None = None
        self.generated_name: str | None = None
