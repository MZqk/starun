import base64
import binascii
import asyncio
import hashlib
from io import BytesIO
import json
import logging
import math
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps
from pydantic import SecretStr

from app.artifacts.contracts import JsonValue
from app.processing.models import ArtDirection, GeneratedArtwork

logger = logging.getLogger(__name__)


IMAGE_GENERATION_SIZES = (
    (2048, 512),
    (1984, 512),
    (1920, 512),
    (1856, 512),
    (1792, 512),
    (1728, 512),
    (1664, 512),
    (1600, 512),
    (1536, 512),
    (1472, 576),
    (1408, 640),
    (1344, 704),
    (1280, 768),
    (1216, 832),
    (1152, 896),
    (1088, 960),
    (1024, 1024),
    (960, 1088),
    (896, 1152),
    (832, 1216),
    (768, 1280),
    (704, 1344),
    (640, 1408),
    (576, 1472),
    (512, 1536),
    (512, 1600),
    (512, 1664),
    (512, 1728),
    (512, 1792),
    (512, 1856),
    (512, 1920),
    (512, 1984),
    (512, 2048),
    (768, 1024),
    (720, 1280),
    (1024, 768),
    (1280, 720),
)


class ImageProviderConfigurationError(RuntimeError):
    pass


class ImageProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class TokenHubImageProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr | None,
        model: str,
        timeout_seconds: float,
        max_response_bytes: int,
        allowed_download_hosts: frozenset[str],
    ) -> None:
        if api_key is None or not api_key.get_secret_value().strip():
            raise ImageProviderConfigurationError("Image generation API key is not configured.")
        self._url = f"{base_url.rstrip('/')}/images/generations"
        self._api_key = api_key.get_secret_value()
        self._model = model
        self._timeout = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._allowed_download_hosts = allowed_download_hosts

    async def generate(
        self,
        *,
        reference_png: bytes,
        direction: ArtDirection,
    ) -> GeneratedArtwork:
        with Image.open(BytesIO(reference_png)) as reference:
            reference_width, reference_height = reference.size
            generation_size = _closest_generation_size(reference_width, reference_height)
        generation_width, generation_height = (
            int(part) for part in generation_size.split("x", maxsplit=1)
        )
        reference_base64 = base64.b64encode(reference_png).decode("ascii")
        prompt = _final_prompt(
            direction,
            reference_width=reference_width,
            reference_height=reference_height,
            generation_width=generation_width,
            generation_height=generation_height,
        )
        request_controls: dict[str, JsonValue] = {
            "image_reference_transport": "ImagesBase64",
            "reference_image_count": 1,
            "revise": 0,
        }
        payload = {
            "Model": self._model,
            "Prompt": prompt,
            "Images": [reference_base64],
            "Resolution": generation_size.replace("x", ":"),
            "LogoAdd": 0,
            "Revise": 0,
        }
        logger.debug(
            "Image generation request payload: url=%s body=%s",
            self._url,
            json.dumps(
                _loggable_generation_payload(
                    payload,
                    reference_png=reference_png,
                    reference_width=reference_width,
                    reference_height=reference_height,
                    generation_width=generation_width,
                    generation_height=generation_height,
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ImageProviderError(
                "image_provider_unreachable",
                "Image generation service is temporarily unreachable.",
                retryable=True,
            ) from exc
        if response.status_code in {401, 403}:
            raise ImageProviderError(
                "image_provider_authentication_failed",
                "Image generation service rejected the configured API key.",
                retryable=False,
            )
        if response.status_code >= 400:
            raise ImageProviderError(
                "image_provider_error",
                (
                    f"Image generation request failed with status {response.status_code}"
                    f"{_response_hint(response)}."
                ),
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        return await self._parse_generation_response(
            response,
            requested_size=(generation_width, generation_height),
            request_controls=request_controls,
        )

    async def _parse_generation_response(
        self,
        response: httpx.Response,
        *,
        requested_size: tuple[int, int],
        request_controls: dict[str, JsonValue],
    ) -> GeneratedArtwork:
        try:
            body = response.json()
        except ValueError as exc:
            raise ImageProviderError(
                "image_provider_invalid_response",
                "Image provider returned an invalid response.",
                retryable=True,
            ) from exc
        first, request_id, revised_prompt = _generation_result_from_response(body)
        b64_json = first.get("b64_json")
        if isinstance(b64_json, str) and b64_json:
            try:
                data = base64.b64decode(b64_json, validate=True)
            except binascii.Error as exc:
                raise ImageProviderError(
                    "image_provider_invalid_response",
                    "Image provider returned invalid image data.",
                    retryable=True,
                ) from exc
            return _decode_image(data, None, request_id, revised_prompt, requested_size, request_controls)
        url = first.get("url")
        if not isinstance(url, str) or not url:
            raise ImageProviderError(
                "image_provider_missing_image",
                "Image provider did not return an image.",
                retryable=True,
            )
        data, host = await self._download(url)
        return _decode_image(data, host, request_id, revised_prompt, requested_size, request_controls)

    async def _download(self, url: str) -> tuple[bytes, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not self._is_allowed_download_host(host):
            raise ImageProviderError(
                "image_provider_untrusted_download",
                f"Image provider returned an untrusted download URL host: {host or 'unknown'}.",
                retryable=False,
            )
        response = await self._download_with_retry(url)
        final_host = (response.url.host or "").lower()
        if not self._is_allowed_download_host(final_host):
            raise ImageProviderError(
                "image_provider_untrusted_download",
                f"Image provider redirected to an untrusted download host: {final_host or 'unknown'}.",
                retryable=False,
            )
        data = response.content
        if len(data) > self._max_response_bytes:
            raise ImageProviderError(
                "image_provider_image_too_large",
                "Generated image exceeds size limit.",
                retryable=False,
            )
        return data, final_host

    def _is_allowed_download_host(self, host: str) -> bool:
        if host in self._allowed_download_hosts:
            return True
        # TokenHub commonly returns Tencent COS temporary URLs whose bucket host
        # and region can vary per request; keep the trust boundary limited to
        # HTTPS COS hosts rather than all myqcloud.com domains.
        parts = host.split(".")
        return (
            len(parts) >= 5
            and parts[-1] == "com"
            and parts[-2] == "myqcloud"
            and "cos" in parts[:-2]
        )

    async def _download_with_retry(self, url: str) -> httpx.Response:
        last_network_error: httpx.TimeoutException | httpx.NetworkError | None = None
        last_response: httpx.Response | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=True,
                    max_redirects=3,
                ) as client:
                    response = await client.get(url)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_network_error = exc
                if attempt < 2:
                    await asyncio.sleep(1.0 + attempt)
                continue
            last_response = response
            if response.status_code < 400:
                return response
            if response.status_code not in {404, 408, 409, 425, 429} and response.status_code < 500:
                break
            if attempt < 2:
                await asyncio.sleep(1.0 + attempt)

        if last_response is not None:
            raise ImageProviderError(
                "image_provider_download_failed",
                (
                    "Generated image download failed with status "
                    f"{last_response.status_code} from host "
                    f"{(last_response.url.host or 'unknown').lower()}"
                    f"{_response_hint(last_response)}."
                ),
                retryable=(
                    last_response.status_code in {404, 408, 409, 425, 429}
                    or last_response.status_code >= 500
                ),
            )
        assert last_network_error is not None
        raise ImageProviderError(
            "image_provider_download_failed",
            f"Generated image download failed: {type(last_network_error).__name__}.",
            retryable=True,
        ) from last_network_error


def _decode_image(
    data: bytes,
    host: str | None,
    request_id: object,
    revised_prompt: object,
    requested_size: tuple[int, int],
    request_controls: dict[str, JsonValue],
) -> GeneratedArtwork:
    if len(data) == 0:
        raise ImageProviderError("image_provider_empty_image", "Generated image is empty.", retryable=True)
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            image_format = (image.format or "").upper()
            provider_width, provider_height = image.size
            normalized = image.size != requested_size
            if normalized:
                normalized_image = ImageOps.fit(
                    image.convert("RGB"),
                    requested_size,
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
                output = BytesIO()
                normalized_image.save(output, format="PNG", optimize=True)
                data = output.getvalue()
                image_format = "PNG"
                width, height = normalized_image.size
            else:
                width, height = image.size
    except OSError as exc:
        raise ImageProviderError(
            "image_provider_invalid_image",
            f"Generated image could not be decoded{_content_hint(data)}.",
            retryable=True,
        ) from exc
    if image_format == "PNG":
        media_type = "image/png"
    elif image_format in {"JPEG", "JPG"}:
        media_type = "image/jpeg"
    else:
        raise ImageProviderError(
            "image_provider_unsupported_image",
            f"Generated image format is unsupported: {image_format or 'unknown'}.",
            retryable=False,
        )
    return GeneratedArtwork(
        data=data,
        media_type=media_type,
        width=width,
        height=height,
        provider_width=provider_width,
        provider_height=provider_height,
        normalized_to_requested_size=normalized,
        provider_request_id=request_id if isinstance(request_id, str) else None,
        revised_prompt=revised_prompt if isinstance(revised_prompt, str) else None,
        source_url_host=host,
        provider_request_controls=request_controls,
    )


def _loggable_generation_payload(
    payload: dict[str, JsonValue],
    *,
    reference_png: bytes,
    reference_width: int,
    reference_height: int,
    generation_width: int,
    generation_height: int,
) -> dict[str, JsonValue]:
    logged: dict[str, JsonValue] = {}
    for key, value in payload.items():
        if key in {"image", "reference_image"} and isinstance(value, str):
            logged[key] = _image_data_url_summary(value, reference_png)
        elif key == "Images" and isinstance(value, list):
            logged[key] = [
                _base64_image_summary(item, reference_png)
                if isinstance(item, str)
                else {"present": False, "type": type(item).__name__}
                for item in value
            ]
        else:
            logged[key] = value
    logged["reference_metadata"] = {
        "width": reference_width,
        "height": reference_height,
        "png_bytes": len(reference_png),
        "sha256": hashlib.sha256(reference_png).hexdigest(),
    }
    logged["requested_generation_size"] = {
        "width": generation_width,
        "height": generation_height,
    }
    return logged


def _base64_image_summary(encoded: str, reference_png: bytes) -> dict[str, JsonValue]:
    return {
        "present": bool(encoded),
        "format": "base64",
        "base64_chars": len(encoded),
        "decoded_bytes": len(reference_png),
        "decoded_sha256": hashlib.sha256(reference_png).hexdigest(),
    }


def _image_data_url_summary(data_url: str, reference_png: bytes) -> dict[str, JsonValue]:
    prefix, separator, encoded = data_url.partition(",")
    return {
        "present": bool(separator and encoded),
        "mime": prefix.removeprefix("data:").removesuffix(";base64"),
        "data_url_prefix": prefix + separator,
        "base64_chars": len(encoded) if separator else 0,
        "decoded_bytes": len(reference_png),
        "decoded_sha256": hashlib.sha256(reference_png).hexdigest(),
    }


def _generation_result_from_response(
    body: object,
) -> tuple[dict[str, object], object, object]:
    if not isinstance(body, dict):
        raise ImageProviderError(
            "image_provider_invalid_response",
            "Image provider returned an invalid response.",
            retryable=True,
        )
    data = body.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0], body.get("request_id"), data[0].get("revised_prompt")

    raw_response = body.get("Response")
    if isinstance(raw_response, dict):
        result_images = raw_response.get("ResultImage")
        if isinstance(result_images, list) and result_images and isinstance(result_images[0], str):
            revised_prompt = raw_response.get("RevisedPrompt")
            prompt = revised_prompt[0] if isinstance(revised_prompt, list) and revised_prompt else None
            return {"url": result_images[0], "revised_prompt": prompt}, raw_response.get("RequestId"), prompt
        job_id = raw_response.get("JobId")
        if isinstance(job_id, str) and job_id:
            raise ImageProviderError(
                "image_provider_async_job_unhandled",
                "Image provider returned an async JobId but no generated image URL.",
                retryable=True,
            )

    raise ImageProviderError(
        "image_provider_invalid_response",
        "Image provider returned an invalid response.",
        retryable=True,
    )


def _final_prompt(
    direction: ArtDirection,
    *,
    reference_width: int,
    reference_height: int,
    generation_width: int,
    generation_height: int,
) -> str:
    return (
        "这是一次基于输入图像的深空天文照片后期增强，而不是重新创作、重绘或生成另一张"
        "同类天体图片。"
        "\n\n请严格逐像素参考原始输入图，完整保留原始视场、构图、裁切范围、方向、主体轮廓、"
        "暗部结构、星云边界、尘埃分布，以及每一颗可见恒星的位置、数量、大小和相对亮度关系。"
        "不得生成、删除、移动、替换、扩写或重塑任何星体、星云、尘埃、暗带、细丝、纹理或背景结构。"
        "\n\n只允许进行真实深空后期范畴内的克制增强，包括：背景降噪、轻微星点控制、非线性拉伸、"
        "色彩校准、色调映射、局部对比度优化、亮度平衡、饱和度增强和轻微清晰度提升。所有调整"
        "都必须基于原图已有信号，不得凭天体名称、常见图库、想象效果或艺术风格补充不存在的结构。"
        "\n\n具体增强目标如下："
        "\n\n1. 背景降噪：对背景区域进行精细降噪，降低彩噪、颗粒感和脏背景，同时保留暗弱星云、"
        "尘埃和渐变层次，避免过度磨皮或塑料感。"
        "\n2. 星点控制：对星点进行温和的掩膜控制和轻微收缩，降低亮星对主体的干扰，但必须保留"
        "所有可见恒星的位置、数量、大小层级和亮度关系，不得制造星点消失、星点位移或新增星点。"
        "\n3. 非线性拉伸：通过克制的非线性拉伸提升暗弱星云结构的可见度，同时保护高光区域，"
        "避免核心过曝、亮部断层、星点膨胀或背景被拉灰。"
        "\n4. 色调映射与对比度优化：优化整体明暗层次和局部对比度，使星云结构更加清晰、立体、"
        "细腻，但不得把弱信号扩写成新的细丝、云气、暗带或不存在的纹理。"
        "\n5. 色彩校准：对背景、星点和星云进行自然的色彩平衡校准，减少偏色和色块污染，使背景"
        "接近中性，星云色彩鲜明但不过度荧光化。"
        "\n6. 微哈勃色倾向：在原始图像已有色彩和通道信息基础上，可以加入轻微的 SHO / Hubble "
        "Palette 色彩倾向，增强青蓝、金黄、橙红等层次表现，但不得强行改造成完全不同的窄带"
        "合成效果，不得覆盖原图真实结构。"
        "\n\n"
        f"参考图尺寸为 {reference_width}x{reference_height}，输出必须严格为 "
        f"{generation_width}x{generation_height}，方向、视场和宽高比必须与输入图一致。"
        "\n\n禁止添加任何文字、标题、签名、Logo、隐式水印、边框、角标、“AI生成”标识或任何装饰元素。"
        "\n\n最终输出应像对同一张真实深空照片进行专业、克制、可信的后期增强：背景更干净，"
        "星点更受控，星云结构更清晰，色彩更鲜明，层次更细腻，视觉冲击力更强，但画面内容"
        "必须与原图保持高度一致。"
        f"\n\n增强目标：{direction.generation_prompt}"
        f"\n\n避免：{direction.negative_prompt}"
    )


def _closest_generation_size(width: int, height: int) -> str:
    target_ratio = width / height
    selected_width, selected_height = min(
        IMAGE_GENERATION_SIZES,
        key=lambda size: (
            abs(math.log((size[0] / size[1]) / target_ratio)),
            -(size[0] * size[1]),
        ),
    )
    return f"{selected_width}x{selected_height}"


def _response_hint(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    length = response.headers.get("content-length")
    hint = f"; content_type={content_type or 'unknown'}"
    if length:
        hint += f"; content_length={length}"
    text = _safe_text(response.content)
    if text:
        hint += f"; body={text}"
    return hint


def _content_hint(data: bytes) -> str:
    text = _safe_text(data)
    return f"; body={text}" if text else ""


def _safe_text(data: bytes) -> str:
    if not data:
        return ""
    sample = data[:200].decode("utf-8", errors="ignore")
    sample = " ".join(sample.split())
    return sample[:160]
