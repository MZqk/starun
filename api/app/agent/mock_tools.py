from io import BytesIO
from typing import ClassVar

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin, TiffImagePlugin
from pydantic import BaseModel, ConfigDict, Field

from app.agent.contracts import JsonValue, TaskContext, ToolResult
from app.artifacts.store import ArtifactStore

WIDTH = 640
HEIGHT = 360
WATERMARK = "STARUN MOCK / 演示结果"


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StrengthArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    strength: float = Field(ge=0.0, le=2.0)


class ColorArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    saturation: float = Field(ge=0.0, le=2.0)


class EvaluateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    seed: int = Field(ge=0)


class ExportArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    seed: int = Field(ge=0)
    style: str = Field(pattern="^(realistic|balanced|artistic)$")


class _ObservationTool:
    version: ClassVar[str] = "v1"

    def __init__(self, name: str, input_model: type[BaseModel]) -> None:
        self.name = name
        self.input_model = input_model

    async def execute(
        self,
        context: TaskContext,
        arguments: BaseModel,
    ) -> ToolResult:
        del context
        return ToolResult(
            observations={
                "tool": self.name,
                "arguments": _json_arguments(arguments),
                "demo": True,
            }
        )


class _EvaluateTool:
    name: ClassVar[str] = "mock.evaluate"
    version: ClassVar[str] = "v1"
    input_model: ClassVar[type[BaseModel]] = EvaluateArguments

    async def execute(
        self,
        context: TaskContext,
        arguments: BaseModel,
    ) -> ToolResult:
        del context
        validated = EvaluateArguments.model_validate(arguments)
        score = 0.70 + (validated.seed % 2501) / 10_000
        return ToolResult(
            observations={"evaluation": "deterministic mock only"},
            metrics={"mock_quality": score},
        )


class _ExportTool:
    name: ClassVar[str] = "mock.export"
    version: ClassVar[str] = "v1"
    input_model: ClassVar[type[BaseModel]] = ExportArguments

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    async def execute(
        self,
        context: TaskContext,
        arguments: BaseModel,
    ) -> ToolResult:
        del context
        validated = ExportArguments.model_validate(arguments)
        image = _demo_image(validated.seed, validated.style)
        names = ["result-demo.tiff", "preview-demo.png", "manifest.json"]
        previous = {
            name: self._store.read_bytes(name) if self._store.exists(name) else None
            for name in names
        }
        try:
            tiff = self._store.write_bytes("result-demo.tiff", _encode_tiff(image))
            png = self._store.write_bytes("preview-demo.png", _encode_png(image))
            manifest = self._store.write_json(
                "manifest.json",
                {
                    "artifacts": [
                        tiff.model_dump(mode="json"),
                        png.model_dump(mode="json"),
                    ],
                    "demo": True,
                    "notice": WATERMARK,
                },
            )
        except BaseException:
            self._rollback(names, previous)
            raise
        return ToolResult(
            observations={
                "export": "bounded synthetic preview",
                "scientific_processing": False,
            },
            artifacts=[tiff, png, manifest],
        )

    def _rollback(
        self,
        names: list[str],
        previous: dict[str, bytes | None],
    ) -> None:
        self._store.delete_many(names)
        for name in names:
            data = previous[name]
            if data is not None:
                self._store.write_bytes(name, data)


def build_mock_tools(store: ArtifactStore) -> list[_ObservationTool | _EvaluateTool | _ExportTool]:
    return [
        _ObservationTool("mock.inspect", NoArguments),
        _ObservationTool("mock.stretch", StrengthArguments),
        _ObservationTool("mock.denoise", StrengthArguments),
        _ObservationTool("mock.sharpen", StrengthArguments),
        _ObservationTool("mock.color", ColorArguments),
        _EvaluateTool(),
        _ExportTool(store),
    ]


def _json_arguments(arguments: BaseModel) -> dict[str, JsonValue]:
    return arguments.model_dump(mode="json")


def _demo_image(seed: int, style: str) -> Image.Image:
    style_colors = {
        "realistic": (36, 68, 96),
        "balanced": (52, 58, 112),
        "artistic": (92, 42, 104),
    }
    red, green, blue = style_colors[style]
    pixels = bytearray(WIDTH * HEIGHT * 3)
    seed_byte = seed & 0xFF
    for y in range(HEIGHT):
        for x in range(WIDTH):
            offset = (y * WIDTH + x) * 3
            pixels[offset] = (red + x // 8 + seed_byte // 8) % 256
            pixels[offset + 1] = (green + y // 5 + seed_byte // 11) % 256
            pixels[offset + 2] = (blue + (x + y) // 12 + seed_byte // 13) % 256
    image = Image.frombytes("RGB", (WIDTH, HEIGHT), bytes(pixels))
    draw = ImageDraw.Draw(image)
    draw.rectangle((18, 18, 622, 82), fill=(12, 14, 22))
    draw.text(
        (34, 31),
        "DEMO ONLY - NO SCIENTIFIC PROCESSING",
        fill=(244, 244, 244),
        font=ImageFont.load_default(size=22),
    )
    draw.rectangle((92, 266, 548, 348), fill=(18, 18, 24), outline=(255, 255, 255), width=3)
    draw.text(
        (118, 282),
        "STARUN MOCK /",
        fill=(255, 255, 255),
        font=ImageFont.load_default(size=34),
    )
    _draw_chinese_watermark(draw, 392, 285)
    return image


def _draw_chinese_watermark(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    glyphs = (
        ("11110", "10100", "11110", "10100", "11110", "00100", "11110"),  # 演
        ("11111", "00100", "11111", "10101", "11111", "00100", "00100"),  # 示
        ("11111", "10001", "11111", "00100", "11111", "10101", "11111"),  # 结
        ("11110", "10010", "11110", "10100", "11110", "00100", "11111"),  # 果
    )
    scale = 5
    for glyph in glyphs:
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit == "1":
                    left = x + column * scale
                    top = y + row * scale
                    draw.rectangle(
                        (left, top, left + scale - 1, top + scale - 1),
                        fill=(255, 255, 255),
                    )
        x += 6 * scale


def _encode_png(image: Image.Image) -> bytes:
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Description", WATERMARK)
    image.save(
        output,
        format="PNG",
        compress_level=9,
        optimize=False,
        pnginfo=metadata,
    )
    return output.getvalue()


def _encode_tiff(image: Image.Image) -> bytes:
    output = BytesIO()
    metadata = TiffImagePlugin.ImageFileDirectory_v2()
    metadata[270] = "STARUN MOCK / DEMO RESULT"
    image.save(
        output,
        format="TIFF",
        compression="raw",
        tiffinfo=metadata,
    )
    return output.getvalue()
