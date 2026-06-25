import json as json_lib
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.analysis.kimi import KimiAnalysisClient
from app.fits.schemas import FitsInspection


def _inspection() -> FitsInspection:
    return FitsInspection.model_validate(
        {
            "hdus": [
                {
                    "index": 0,
                    "name": "PRIMARY",
                    "kind": "primary_image",
                    "shape": [16, 16],
                    "dtype": "float32",
                    "supported": True,
                }
            ],
            "selected_hdu": {
                "index": 0,
                "name": "PRIMARY",
                "kind": "primary_image",
                "shape": [16, 16],
                "dtype": "float32",
                "supported": True,
            },
            "statistics": {
                "minimum": 0.0,
                "maximum": 1.0,
                "mean": 0.4,
                "median": 0.35,
                "standard_deviation": 0.2,
                "finite_pixel_count": 256,
            },
            "header": {"OBJECT": "M42"},
        }
    )


def _analysis_response() -> dict[str, Any]:
    return {
        "overview": "整体信号可辨，适合进入基础后期流程。",
        "image_quality": {
            "rating": "good",
            "summary": "背景和主体均有可用信息。",
            "confidence": 0.8,
        },
        "observations": {
            "target": "主体结构清晰。",
            "background": "背景较平整。",
            "stars": "星点形态基本正常。",
            "noise": "噪声水平中等。",
            "color": "颜色判断需结合原始数据。",
        },
        "issues": [
            {
                "title": "背景噪声",
                "severity": "medium",
                "evidence": "统计值显示背景存在波动。",
                "recommendation": "先进行温和降噪。",
            }
        ],
        "workflow": [
            {
                "order": 1,
                "step": "背景校正",
                "purpose": "稳定背景亮度。",
                "guidance": "使用低强度背景建模并检查残余梯度。",
            }
        ],
        "caveats": ["预览图经过拉伸，视觉判断存在不确定性。"],
    }


@pytest.mark.asyncio
async def test_professional_analysis_enables_model_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

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
                    "choices": [
                        {
                            "message": {
                                "content": json_lib.dumps(
                                    _analysis_response(),
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    client = KimiAnalysisClient(
        base_url="https://api.moonshot.cn/v1",
        api_key=SecretStr("secret"),
        model="kimi-k2.6",
        timeout_seconds=30,
    )

    result = await client.analyze(
        preview_png=b"png",
        inspection=_inspection(),
        preview_metadata={"width": 16, "height": 16},
    )

    assert captured["url"] == "https://api.moonshot.cn/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["json"]["model"] == "kimi-k2.6"
    assert captured["json"]["thinking"] == {"type": "enabled"}
    assert result.image_quality.rating == "good"
