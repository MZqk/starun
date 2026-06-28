import logging

from agents import set_tracing_disabled
from agents.models.interface import Model
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI

from app.agent_sdk.errors import AgentNotConfiguredError
from app.config import AgentProtocol, Settings

logger = logging.getLogger(__name__)


def build_agent_model(
    settings: Settings,
    *,
    timeout_seconds: float | None = None,
) -> Model:
    if settings.ai_api_key is None:
        raise AgentNotConfiguredError("Agent provider API key is not configured.")
    api_key = settings.ai_api_key.get_secret_value().strip()
    if not api_key:
        raise AgentNotConfiguredError("Agent provider API key is not configured.")

    set_tracing_disabled(True)
    logger.debug(
        "Building OpenAI agent model: base_url=%s model=%s protocol=%s timeout_seconds=%s max_retries=%s",
        settings.ai_base_url.rstrip("/") + "/",
        settings.ai_model,
        settings.agent_protocol.value,
        timeout_seconds or settings.ai_timeout_seconds,
        0,
    )
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=settings.ai_base_url.rstrip("/") + "/",
        timeout=timeout_seconds or settings.ai_timeout_seconds,
        max_retries=0,
    )
    if settings.agent_protocol is AgentProtocol.RESPONSES:
        return OpenAIResponsesModel(
            model=settings.ai_model,
            openai_client=client,
        )
    return OpenAIChatCompletionsModel(
        model=settings.ai_model,
        openai_client=client,
    )
