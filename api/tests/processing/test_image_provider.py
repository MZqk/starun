import base64
from io import BytesIO
import logging
from typing import Any

import httpx
import pytest
from PIL import Image
from pydantic import SecretStr

from app.processing.image_provider import (
    ImageProviderError,
    TokenHubImageProvider,
    _closest_generation_size,
)
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
    assert captured["json"]["Resolution"] == "1024:1024"
    assert captured["json"]["Model"] == "hy-image-v3.0"
    assert captured["json"]["Revise"] == 0
    assert isinstance(captured["json"]["Images"], list)
    assert len(captured["json"]["Images"]) == 1
    assert not captured["json"]["Images"][0].startswith("data:")
    assert base64.b64decode(captured["json"]["Images"][0], validate=True) == _png_bytes()
    assert "image" not in captured["json"]
    assert "reference_image" not in captured["json"]
    assert "不是重新创作、重绘或生成另一张同类天体图片" in captured["json"]["Prompt"]
    assert "不得生成、删除、移动、替换、扩写或重塑任何星体" in captured["json"]["Prompt"]
    assert "方向、视场和宽高比必须与输入图一致" in captured["json"]["Prompt"]
    assert "微哈勃色倾向" in captured["json"]["Prompt"]
    assert "禁止添加任何文字、标题、签名、Logo、隐式水印" in captured["json"]["Prompt"]
    assert artwork.provider_request_id == "req-1"
    assert artwork.revised_prompt == "revised"
    assert artwork.media_type == "image/png"
    assert artwork.provider_width == 2
    assert artwork.provider_height == 2
    assert artwork.width == 1024
    assert artwork.height == 1024
    assert artwork.normalized_to_requested_size
    assert artwork.provider_request_controls == {
        "image_reference_transport": "ImagesBase64",
        "reference_image_count": 1,
        "revise": 0,
    }


@pytest.mark.asyncio
async def test_tokenhub_generation_logs_request_payload_summary(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = _png_bytes()

    class CapturingAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "CapturingAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "b64_json": base64.b64encode(generated).decode("ascii"),
                        }
                    ]
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
    reference = _png_bytes()

    with caplog.at_level(logging.DEBUG, logger="app.processing.image_provider"):
        await provider.generate(reference_png=reference, direction=_direction())

    message = next(
        record.getMessage()
        for record in caplog.records
        if "Image generation request payload:" in record.getMessage()
    )
    assert '"Prompt":' in message
    assert "不是重新创作、重绘或生成另一张同类天体图片" in message
    assert '"Images": [{"base64_chars":' in message
    assert '"decoded_bytes":' in message
    assert '"decoded_sha256":' in message
    assert "iVBOR" not in message


@pytest.mark.asyncio
async def test_tokenhub_normalizes_provider_output_to_reference_ratio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = BytesIO()
    Image.new("RGB", (2160, 3840), (8, 16, 32)).save(reference, format="PNG")
    generated = BytesIO()
    Image.new("RGB", (1024, 1024), (24, 32, 48)).save(generated, format="PNG")

    class SquareOutputAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "SquareOutputAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "b64_json": base64.b64encode(generated.getvalue()).decode("ascii"),
                        }
                    ]
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", SquareOutputAsyncClient)
    provider = TokenHubImageProvider(
        base_url="https://tokenhub.tencentmaas.com/v1",
        api_key=SecretStr("secret"),
        model="hy-image-v3.0",
        timeout_seconds=30,
        max_response_bytes=10 * 1024 * 1024,
        allowed_download_hosts=frozenset({"tokenhub.tencentmaas.com"}),
    )

    artwork = await provider.generate(
        reference_png=reference.getvalue(),
        direction=_direction(),
    )

    assert (artwork.provider_width, artwork.provider_height) == (1024, 1024)
    assert (artwork.width, artwork.height) == (720, 1280)
    assert artwork.normalized_to_requested_size
    with Image.open(BytesIO(artwork.data)) as image:
        assert image.size == (720, 1280)


@pytest.mark.asyncio
async def test_tokenhub_accepts_tencent_cloud_result_image_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = _png_bytes()

    class TencentCloudShapeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "TencentCloudShapeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "Response": {
                        "RequestId": "tc-req",
                        "JobStatusCode": "5",
                        "ResultImage": [
                            "https://aiart-1258344699.cos.ap-guangzhou.myqcloud.com/result.png"
                        ],
                        "RevisedPrompt": ["revised prompt"],
                    }
                },
            )

        async def get(self, *args: Any, **kwargs: Any) -> httpx.Response:
            return httpx.Response(
                200,
                content=generated,
                headers={"content-type": "image/png"},
                request=httpx.Request(
                    "GET",
                    "https://aiart-1258344699.cos.ap-guangzhou.myqcloud.com/result.png",
                ),
            )

    monkeypatch.setattr(httpx, "AsyncClient", TencentCloudShapeAsyncClient)
    provider = TokenHubImageProvider(
        base_url="https://tokenhub.tencentmaas.com/v1",
        api_key=SecretStr("secret"),
        model="hy-image-v3.0",
        timeout_seconds=30,
        max_response_bytes=10 * 1024 * 1024,
        allowed_download_hosts=frozenset({"tokenhub.tencentmaas.com"}),
    )

    artwork = await provider.generate(reference_png=_png_bytes(), direction=_direction())

    assert artwork.provider_request_id == "tc-req"
    assert artwork.revised_prompt == "revised prompt"
    assert artwork.source_url_host == "aiart-1258344699.cos.ap-guangzhou.myqcloud.com"


@pytest.mark.asyncio
async def test_tokenhub_does_not_retry_without_reference_image_when_provider_rejects_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []

    class RejectingImagesAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "RejectingImagesAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(
            self,
            *args: Any,
            json: dict[str, Any],
            **kwargs: Any,
        ) -> httpx.Response:
            del args, kwargs
            requests.append(json)
            return httpx.Response(
                400,
                json={"error": {"message": "unknown field Images"}},
            )

    monkeypatch.setattr(httpx, "AsyncClient", RejectingImagesAsyncClient)
    provider = TokenHubImageProvider(
        base_url="https://tokenhub.tencentmaas.com/v1",
        api_key=SecretStr("secret"),
        model="hy-image-v3.0",
        timeout_seconds=30,
        max_response_bytes=10 * 1024 * 1024,
        allowed_download_hosts=frozenset({"tokenhub.tencentmaas.com"}),
    )

    with pytest.raises(ImageProviderError) as caught:
        await provider.generate(reference_png=_png_bytes(), direction=_direction())

    assert caught.value.code == "image_provider_error"
    assert len(requests) == 1
    assert "Images" in requests[0]


@pytest.mark.asyncio
async def test_tokenhub_rejects_invalid_api_key_with_specific_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnauthorizedAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "UnauthorizedAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    monkeypatch.setattr(httpx, "AsyncClient", UnauthorizedAsyncClient)
    provider = TokenHubImageProvider(
        base_url="https://tokenhub.tencentmaas.com/v1",
        api_key=SecretStr("invalid"),
        model="hy-image-v3.0",
        timeout_seconds=30,
        max_response_bytes=1024 * 1024,
        allowed_download_hosts=frozenset({"tokenhub.tencentmaas.com"}),
    )

    with pytest.raises(ImageProviderError) as caught:
        await provider.generate(reference_png=_png_bytes(), direction=_direction())

    assert caught.value.code == "image_provider_authentication_failed"
    assert not caught.value.retryable


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (3840, 2160, "1280x720"),
        (2160, 3840, "720x1280"),
        (2048, 2048, "1024x1024"),
        (3000, 2000, "1216x832"),
    ],
)
def test_closest_generation_size_preserves_reference_ratio(
    width: int,
    height: int,
    expected: str,
) -> None:
    assert _closest_generation_size(width, height) == expected
