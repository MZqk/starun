from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from app.agent.contracts import TaskContext, ToolResult
from app.analysis import render_fits_preview
from app.artifacts.contracts import JsonValue
from app.artifacts.store import ArtifactStore
from app.config import Settings
from app.db.models import ProcessingStyle
from app.processing.art_direction import KimiArtDirectionClient
from app.processing.image_provider import TokenHubImageProvider
from app.processing.models import ProcessingState


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PrepareReferenceTool:
    name: ClassVar[str] = "processing.prepare_reference"
    version: ClassVar[str] = "v1"
    input_model: ClassVar[type[BaseModel]] = NoArguments

    def __init__(self, store: ArtifactStore, settings: Settings, state: ProcessingState) -> None:
        self._store = store
        self._settings = settings
        self._state = state

    async def execute(self, context: TaskContext, arguments: BaseModel) -> ToolResult:
        del arguments
        if context.fits_inspection is None:
            raise ValueError("FITS inspection is required for processing")
        preview = render_fits_preview(
            context.source_path,
            context.fits_inspection.selected_hdu.index,
            max_edge=self._settings.image_ai_max_edge,
        )
        artifact = self._store.write_bytes("processing-reference.png", preview.data)
        self._state.reference_name = artifact.name
        self._state.reference_width = preview.width
        self._state.reference_height = preview.height
        self._state.reference_png = preview.data
        return ToolResult(
            observations={
                "artifact": artifact.name,
                "width": preview.width,
                "height": preview.height,
                "lower_percentile_value": preview.lower_percentile,
                "upper_percentile_value": preview.upper_percentile,
            },
            artifacts=[artifact],
        )


class PlanArtDirectionTool:
    name: ClassVar[str] = "processing.plan_art_direction"
    version: ClassVar[str] = "v1"
    input_model: ClassVar[type[BaseModel]] = NoArguments

    def __init__(
        self,
        store: ArtifactStore,
        state: ProcessingState,
        client: KimiArtDirectionClient,
    ) -> None:
        self._store = store
        self._state = state
        self._client = client

    async def execute(self, context: TaskContext, arguments: BaseModel) -> ToolResult:
        del arguments
        if context.fits_inspection is None or self._state.reference_png is None:
            raise ValueError("reference preview is required before art direction")
        direction = await self._client.create_direction(
            reference_png=self._state.reference_png,
            inspection=context.fits_inspection,
            style=context.style or ProcessingStyle.BALANCED,
            preview_metadata={
                "width": self._state.reference_width or 0,
                "height": self._state.reference_height or 0,
            },
        )
        artifact = self._store.write_json(
            "art-direction.json",
            direction.model_dump(mode="json"),
        )
        self._state.direction = direction
        return ToolResult(
            observations={
                "artifact": artifact.name,
                "target_summary": direction.target_summary,
                "visible_subject": direction.visible_subject,
                "edit_intensity": direction.edit_intensity,
            },
            artifacts=[artifact],
        )


class GenerateArtworkTool:
    name: ClassVar[str] = "processing.generate_artwork"
    version: ClassVar[str] = "v1"
    input_model: ClassVar[type[BaseModel]] = NoArguments

    def __init__(
        self,
        store: ArtifactStore,
        state: ProcessingState,
        provider: TokenHubImageProvider,
    ) -> None:
        self._store = store
        self._state = state
        self._provider = provider

    async def execute(self, context: TaskContext, arguments: BaseModel) -> ToolResult:
        del arguments, context
        if self._state.reference_png is None or self._state.direction is None:
            raise ValueError("art direction is required before image generation")
        generated = await self._provider.generate(
            reference_png=self._state.reference_png,
            direction=self._state.direction,
        )
        name = "generated-artwork.jpg" if generated.media_type == "image/jpeg" else "generated-artwork.png"
        image_artifact = self._store.write_bytes(name, generated.data)
        record: dict[str, JsonValue] = {
            "artifact": image_artifact.model_dump(mode="json"),
            "width": generated.width,
            "height": generated.height,
            "media_type": generated.media_type,
            "provider_request_id": generated.provider_request_id,
            "revised_prompt": generated.revised_prompt,
            "source_url_host": generated.source_url_host,
        }
        record_artifact = self._store.write_json("generation-record.json", record)
        self._state.generated = generated
        self._state.generated_name = image_artifact.name
        return ToolResult(
            observations={
                "artifact": image_artifact.name,
                "width": generated.width,
                "height": generated.height,
                "media_type": generated.media_type,
                "provider_request_id": generated.provider_request_id,
            },
            artifacts=[image_artifact, record_artifact],
            metrics={"ai_art_quality": 0.78},
        )
