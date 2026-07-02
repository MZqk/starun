from collections.abc import Awaitable, Callable

from app.agent.contracts import AgentEvent, AgentPlan, AgentStep, JsonValue, TaskContext, ToolResult
from app.agent.registry import ToolRegistry
from app.agent.runner import AgentRunner
from app.artifacts.store import ArtifactStore
from app.config import Settings
from app.db.models import ProcessingStyle
from app.processing.art_direction import StarunAgentModelArtDirectionClient
from app.processing.image_provider import TokenHubImageProvider
from app.processing.models import ARTWORK_DISCLAIMER, ProcessingState
from app.processing.tools import GenerateArtworkTool, PlanArtDirectionTool, PrepareReferenceTool


class FixedProcessingModel:
    def __init__(self, settings: Settings, state: ProcessingState) -> None:
        self._settings = settings
        self._state = state

    async def plan(self, context: TaskContext) -> AgentPlan:
        if context.style is ProcessingStyle.ARTISTIC:
            return AgentPlan(
                version="1",
                max_iterations=1,
                steps=[
                    _step("01", "processing.prepare_reference"),
                    _step("02", "processing.generate_artwork"),
                ],
            )
        return AgentPlan(
            version="1",
            max_iterations=1,
            steps=[
                _step("01", "processing.prepare_reference"),
                _step("02", "processing.plan_art_direction"),
                _step("03", "processing.generate_artwork"),
            ],
        )

    async def evaluate(self, observation: ToolResult) -> float:
        return observation.metrics.get("ai_art_quality", 0.72)

    async def summarize(
        self,
        context: TaskContext,
        observation: ToolResult,
    ) -> dict[str, JsonValue]:
        del observation
        style = context.style or ProcessingStyle.BALANCED
        direction = self._state.direction
        generated = self._state.generated
        return {
            "mode": "generative_art_enhancement",
            "demo": False,
            "style": style.value,
            "art_direction_model": None if style is ProcessingStyle.ARTISTIC else self._settings.ai_model,
            "image_model": self._settings.image_ai_model,
            "target_summary": direction.target_summary if direction else "",
            "visible_subject": direction.visible_subject if direction else "",
            "art_direction_summary": direction.generation_prompt if direction else "",
            "reference_artifact": self._state.reference_name or "processing-reference.png",
            "result_artifact": self._state.generated_name or "generated-artwork.png",
            "result_width": generated.width if generated else 0,
            "result_height": generated.height if generated else 0,
            "provider_request_id": generated.provider_request_id if generated else None,
            "provider_request_controls": generated.provider_request_controls if generated else {},
            "disclaimer": ARTWORK_DISCLAIMER,
        }


def build_processing_runner(
    artifact_store: ArtifactStore,
    *,
    settings: Settings,
    event_sink: Callable[[AgentEvent], Awaitable[None] | None] | None = None,
) -> AgentRunner:
    state = ProcessingState()
    direction_client = StarunAgentModelArtDirectionClient(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        timeout_seconds=settings.ai_timeout_seconds,
    )
    image_provider = TokenHubImageProvider(
        base_url=settings.image_ai_base_url,
        api_key=settings.image_ai_api_key,
        model=settings.image_ai_model,
        timeout_seconds=settings.image_ai_timeout_seconds,
        max_response_bytes=settings.image_ai_max_response_bytes,
        allowed_download_hosts=settings.allowed_image_download_hosts,
    )
    return AgentRunner(
        model=FixedProcessingModel(settings, state),
        registry=ToolRegistry(
            [
                PrepareReferenceTool(artifact_store, settings, state),
                PlanArtDirectionTool(artifact_store, state, direction_client),
                GenerateArtworkTool(artifact_store, state, image_provider),
            ]
        ),
        artifact_store=artifact_store,
        event_sink=event_sink,
    )


def _step(identifier: str, tool_name: str) -> AgentStep:
    return AgentStep(
        id=identifier,
        tool_name=tool_name,
        tool_version="v1",
        arguments={},
    )
