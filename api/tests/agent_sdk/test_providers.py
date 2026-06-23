from typing import Any

from pydantic import SecretStr

from app.agent_sdk import providers
from app.config import Settings


def test_build_agent_model_accepts_art_direction_timeout(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    class FakeChatCompletionsModel:
        def __init__(self, **kwargs: object) -> None:
            captured["model_kwargs"] = kwargs

    monkeypatch.setattr(providers, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr(
        providers,
        "OpenAIChatCompletionsModel",
        FakeChatCompletionsModel,
    )

    settings = Settings(
        _env_file=None,
        ai_api_key=SecretStr("test-key"),
        ai_timeout_seconds=180,
    )

    providers.build_agent_model(settings, timeout_seconds=600)

    assert captured["timeout"] == 600
    assert captured["max_retries"] == 0
