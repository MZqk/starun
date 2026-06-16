#!/usr/bin/env python3
import argparse
import asyncio
import base64
import json
import sys
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings


PROMPT = (
    "Preserve the exact composition and aspect ratio. Apply a restrained "
    "deep-space color grade. Do not add or remove objects. "
    "This is a synthetic API probe."
)


def synthetic_reference() -> bytes:
    image = Image.new("RGB", (512, 320), "#080812")
    draw = ImageDraw.Draw(image)
    draw.ellipse((175, 70, 340, 245), fill="#7c284f")
    draw.text((16, 16), "SYNTHETIC PROVIDER PROBE", fill="white")
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _top_level_keys(value: Any) -> list[str]:
    return sorted(value) if isinstance(value, dict) else []


def _error_message(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    error = value.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message[:300]
    message = value.get("message")
    return message[:300] if isinstance(message, str) else None


def _request_id(response: httpx.Response, value: Any) -> str | None:
    for name in ("x-request-id", "request-id", "x-tencent-request-id"):
        candidate = response.headers.get(name)
        if candidate:
            return candidate
    if isinstance(value, dict):
        for name in ("request_id", "id", "task_id"):
            candidate = value.get(name)
            if isinstance(candidate, str):
                return candidate
    return None


def _candidate_payloads(value: Any) -> list[bytes]:
    if not isinstance(value, dict):
        return []
    candidates: list[Any] = []
    data = value.get("data")
    if isinstance(data, list):
        candidates.extend(data)
    output = value.get("output")
    if isinstance(output, dict):
        candidates.append(output)
    candidates.append(value)
    decoded: list[bytes] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("b64_json", "base64", "image_base64"):
            raw = candidate.get(key)
            if isinstance(raw, str):
                try:
                    decoded.append(base64.b64decode(raw, validate=True))
                except ValueError:
                    pass
    return decoded


def _data_shapes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        return []
    shapes: list[dict[str, Any]] = []
    for item in value["data"][:3]:
        if not isinstance(item, dict):
            shapes.append({"type": type(item).__name__})
            continue
        urls = [
            raw
            for key in ("url", "image_url")
            if isinstance((raw := item.get(key)), str)
        ]
        shapes.append(
            {
                "keys": sorted(item),
                "url_hosts": [urlparse(url).hostname for url in urls],
            }
        )
    return shapes


def _candidate_urls(value: Any) -> list[str]:
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        return []
    urls: list[str] = []
    for item in value["data"]:
        if not isinstance(item, dict):
            continue
        for key in ("url", "image_url"):
            raw = item.get(key)
            if isinstance(raw, str):
                urls.append(raw)
    return urls


def _decode_image(candidates: list[bytes]) -> tuple[str, tuple[int, int]] | None:
    for candidate in candidates:
        try:
            with Image.open(BytesIO(candidate)) as image:
                image.verify()
            with Image.open(BytesIO(candidate)) as image:
                image.load()
                return image.format or "unknown", image.size
        except (OSError, ValueError):
            continue
    return None


async def _attempt(
    client: httpx.AsyncClient,
    *,
    name: str,
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> bool:
    response = await client.request(
        method,
        path,
        json=json_body,
        data=data,
        files=files,
    )
    try:
        body = response.json()
    except ValueError:
        body = None
    summary = {
        "attempt": name,
        "status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "top_level_keys": _top_level_keys(body),
        "request_id": _request_id(response, body),
        "error": _error_message(body),
        "data_shapes": _data_shapes(body),
    }
    decoded = _decode_image(_candidate_payloads(body))
    if decoded is None:
        for url in _candidate_urls(body):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname is None:
                continue
            image_response = await client.get(url)
            if image_response.is_success:
                decoded = _decode_image([image_response.content])
            if decoded is not None:
                break
    if decoded is not None:
        summary["image_format"], summary["image_dimensions"] = decoded
    print(json.dumps(summary, ensure_ascii=False))
    return response.is_success and decoded is not None


async def probe(settings: Settings) -> bool:
    if settings.image_ai_api_key is None:
        raise RuntimeError("STARUN_IMAGE_AI_API_KEY is not configured")
    reference = synthetic_reference()
    data_url = "data:image/png;base64," + base64.b64encode(reference).decode("ascii")
    headers = {
        "Authorization": f"Bearer {settings.image_ai_api_key.get_secret_value()}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(
        base_url=settings.image_ai_base_url.rstrip("/") + "/",
        headers=headers,
        timeout=settings.image_ai_timeout_seconds,
        follow_redirects=False,
    ) as client:
        attempts = [
            {
                "name": "multipart-edits",
                "method": "POST",
                "path": "images/edits",
                "data": {
                    "model": settings.image_ai_model,
                    "prompt": PROMPT,
                    "response_format": "b64_json",
                },
                "files": {"image": ("probe.png", reference, "image/png")},
            },
            {
                "name": "json-generations-image",
                "method": "POST",
                "path": "images/generations",
                "json_body": {
                    "model": settings.image_ai_model,
                    "prompt": PROMPT,
                    "image": data_url,
                    "response_format": "b64_json",
                    "n": 1,
                },
            },
            {
                "name": "json-generations-reference-image",
                "method": "POST",
                "path": "images/generations",
                "json_body": {
                    "model": settings.image_ai_model,
                    "prompt": PROMPT,
                    "reference_image": data_url,
                    "response_format": "b64_json",
                    "n": 1,
                },
            },
        ]
        for attempt in attempts:
            if await _attempt(client, **attempt):
                return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe reference-image support using a synthetic image."
    )
    parser.parse_args()
    try:
        supported = asyncio.run(probe(Settings()))
    except (httpx.HTTPError, RuntimeError) as exc:
        print(json.dumps({"probe_error": str(exc)[:300]}, ensure_ascii=False))
        return 2
    return 0 if supported else 1


if __name__ == "__main__":
    raise SystemExit(main())
