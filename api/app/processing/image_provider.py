import base64
import binascii
import asyncio
from io import BytesIO
from urllib.parse import urlparse

import httpx
from PIL import Image
from pydantic import SecretStr

from app.processing.models import ArtDirection, GeneratedArtwork


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
        payload = {
            "model": self._model,
            "prompt": _final_prompt(direction),
            "image": "data:image/png;base64," + base64.b64encode(reference_png).decode("ascii"),
            "response_format": "b64_json",
            "n": 1,
        }
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
        if response.status_code >= 400:
            raise ImageProviderError(
                "image_provider_error",
                (
                    f"Image generation request failed with status {response.status_code}"
                    f"{_response_hint(response)}."
                ),
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        return await self._parse_generation_response(response)

    async def _parse_generation_response(self, response: httpx.Response) -> GeneratedArtwork:
        try:
            body = response.json()
            first = body["data"][0]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ImageProviderError(
                "image_provider_invalid_response",
                "Image provider returned an invalid response.",
                retryable=True,
            ) from exc
        request_id = body.get("request_id")
        revised_prompt = first.get("revised_prompt")
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
            return _decode_image(data, None, request_id, revised_prompt)
        url = first.get("url")
        if not isinstance(url, str) or not url:
            raise ImageProviderError(
                "image_provider_missing_image",
                "Image provider did not return an image.",
                retryable=True,
            )
        data, host = await self._download(url)
        return _decode_image(data, host, request_id, revised_prompt)

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
) -> GeneratedArtwork:
    if len(data) == 0:
        raise ImageProviderError("image_provider_empty_image", "Generated image is empty.", retryable=True)
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            image_format = (image.format or "").upper()
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
        provider_request_id=request_id if isinstance(request_id, str) else None,
        revised_prompt=revised_prompt if isinstance(revised_prompt, str) else None,
        source_url_host=host,
    )


def _final_prompt(direction: ArtDirection) -> str:
    return (
        "以提供的参考天文图像为强约束生成最终成片。保持主体位置、视场关系、星点分布和"
        "主要结构，不要更换天体、不要加入文字、水印、边框或科幻元素。输出应像真实深空"
        "摄影后期成片，而非插画。"
        f"\n\n增强目标：{direction.generation_prompt}"
        f"\n\n避免：{direction.negative_prompt}"
    )


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
