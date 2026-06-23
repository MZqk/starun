import base64
from io import BytesIO
from typing import Any

import httpx
import pytest
from PIL import Image
from pydantic import SecretStr

from app.processing.image_provider import TokenHubImageProvider
from app.processing.models import ArtDirection


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (8, 16, 32)).save(buffer, format="PNG")
    return buffer.getvalue()


def _direction() -> ArtDirection:
    return ArtDirection(
        target_summary="M42 artistic preview",
        visible_subject="Orion nebula core and surrounding faint dust",
        quality_notes=["reference preview is low contrast"],
        generation_prompt=(
            "增强星云层次、色彩和背景通透度，保持原始构图、主体位置和自然星点分布。"
        ),
        negative_prompt="水印、文字、边框、伪结构、过饱和、塑料感、星点变形",
        edit_intensity="high",
    )


@pytest.mark.asyncio
async def test_tokenhub_generation_disables_explicit_hunyuan_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    generated = _png_bytes()

    class CapturingAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self) -> "CapturingAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> httpx.Response:
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return httpx.Response(
                200,
                json={
                    "request_id": "req-1",
                    "data": [
                        {
                            "b64_json": base64.b64encode(generated).decode("ascii"),
                            "revised_prompt": "revised",
                        }
                    ],
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    provider = TokenHubImageProvider(
        base_url="https://tokenhub.tencentmaas.com/v1",
        api_key=SecretStr("secret"),
        model="hy-image-v3.0",
        timeout_seconds=30,
        max_response_bytes=1024 * 1024,
        allowed_download_hosts=frozenset({"tokenhub.tencentmaas.com"}),
    )

    artwork = await provider.generate(reference_png=_png_bytes(), direction=_direction())

    assert captured["url"] == "https://tokenhub.tencentmaas.com/v1/images/generations"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["json"]["LogoAdd"] == 0
    assert captured["json"]["model"] == "hy-image-v3.0"
    assert captured["json"]["response_format"] == "b64_json"
    assert artwork.provider_request_id == "req-1"
    assert artwork.revised_prompt == "revised"
    assert artwork.media_type == "image/png"
