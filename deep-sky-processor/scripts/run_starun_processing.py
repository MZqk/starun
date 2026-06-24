#!/usr/bin/env python3
"""Run Deep Sky Processor as a Starun Agents SDK skill entrypoint.

The standalone skill remains agent-in-the-loop. This wrapper is the compact
automation entrypoint used by Starun's API sandbox so the model does not spend
turns orchestrating individual scripts.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import pipeline
import recognize


MEDIA_TYPES = {
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _workspace_root() -> Path:
    candidates = [Path.cwd(), SCRIPT_DIR, *Path.cwd().parents, *SCRIPT_DIR.parents]
    for candidate in candidates:
        if (candidate / "input" / "request.json").is_file() or (candidate / "input").is_dir():
            return candidate
    return Path.cwd()


def _sandbox_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    workspace = _workspace_root()
    cwd_relative = (Path.cwd() / path).resolve()
    try:
        cwd_relative.relative_to(workspace.resolve())
        return cwd_relative
    except ValueError:
        pass
    if path.exists():
        return path
    return workspace / path


def _trim(value: object, limit: int) -> str:
    text = " ".join(str(value).split())
    return text[:limit].rstrip() if len(text) > limit else text


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_failure(
    result_path: Path,
    error_code: str,
    message: str,
    *,
    retryable: bool = False,
    missing_dependencies: list[str] | None = None,
) -> None:
    _write_json(
        result_path,
        {
            "schema_version": "starun.skill-result/v1",
            "status": "failed",
            "error_code": error_code,
            "message": _trim(message, 1000),
            "retryable": retryable,
            "missing_dependencies": missing_dependencies or [],
        },
    )


def _artifact(name: str) -> dict[str, str]:
    suffix = Path(name).suffix.lower()
    media_type = MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise ValueError(f"unsupported artifact suffix: {suffix}")
    return {"name": name, "media_type": media_type}


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(item) for item in value]
        return str(value)


def _quality_score(status: str, quality_gates: list[dict[str, Any]]) -> float:
    score = {
        "success": 0.86,
        "partial_success": 0.72,
        "review_required": 0.58,
        "failed": 0.0,
    }.get(status, 0.55)
    for gate in quality_gates:
        gate_status = str(gate.get("status", "")).lower()
        severity = str(gate.get("severity", "")).lower()
        if gate_status in {"fail", "failed", "error"} or severity == "high":
            score -= 0.12
        elif gate_status in {"warn", "warning"} or severity == "medium":
            score -= 0.05
    return max(0.0, min(1.0, round(score, 3)))


def _result_size(path: Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None, None


def _write_reference(source_path: Path, output_dir: Path) -> str:
    preview_dir = output_dir / "reference_workflow" / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    bundle = recognize.create_safe_preview_bundle(source_path, preview_dir)
    safe_full = Path(bundle["paths"]["full"])
    reference_path = output_dir / "reference.jpg"
    with Image.open(safe_full) as image:
        image.convert("RGB").save(reference_path, format="JPEG", quality=92)
    return reference_path.name


def _style_prompt(
    inspection: dict[str, Any],
    pipeline_result: dict[str, Any],
) -> dict[str, Any]:
    config = pipeline_result.get("effective_config", {})
    if not isinstance(config, dict):
        config = {}
    target_type = config.get("target_type") or "unknown"
    target_name = config.get("target_name") or "unknown"
    return {
        "schema_version": "starun.style-prompt/v1",
        "target": {
            "name": target_name,
            "type": target_type,
            "source_format": inspection.get("format") or inspection.get("file_type") or "unknown",
        },
        "style": {
            "mode": "balanced",
            "profile": config.get("style") or "auto",
            "strength": config.get("style_strength", 1.0),
        },
        "guidance": (
            "在不生成新结构的前提下，平衡细节、降噪、色彩、对比度和星点自然度；"
            "避免过度饱和、背景压黑、星点硬边、局部增强光晕和伪造窄带色彩。"
        ),
        "avoid": [
            "invented_nebulosity",
            "over_blackened_background",
            "clipped_star_cores",
            "halo_artifacts",
            "false_narrowband_palette",
        ],
    }


def _target_summary(pipeline_result: dict[str, Any], inspection: dict[str, Any]) -> str:
    config = pipeline_result.get("effective_config", {})
    if not isinstance(config, dict):
        config = {}
    target_name = config.get("target_name") or "未识别目标"
    target_type = config.get("target_type") or "unknown"
    shape = inspection.get("shape") or inspection.get("image_shape") or "unknown"
    return _trim(f"{target_name} / {target_type}，输入尺寸 {shape}。", 240)


def _visible_subject(pipeline_result: dict[str, Any]) -> str:
    config = pipeline_result.get("effective_config", {})
    if not isinstance(config, dict):
        config = {}
    steps = config.get("steps") or []
    if isinstance(steps, list):
        step_text = "、".join(str(step) for step in steps)
    else:
        step_text = str(steps)
    return _trim(
        f"非生成式深空后期结果，管线步骤：{step_text or 'adaptive'}；"
        f"风格：{config.get('style', 'auto')}，强度：{config.get('style_strength', 'unknown')}。",
        512,
    )


def _art_direction_summary(style: str, pipeline_result: dict[str, Any]) -> str:
    config = pipeline_result.get("effective_config", {})
    if not isinstance(config, dict):
        config = {}
    if style == "realistic":
        summary = (
            "写实模式：采用保守的非生成式天文后期，优先保持原始构图、星点分布、"
            "自然色彩和真实弱信号，不调用生图模型。"
        )
    else:
        summary = (
            "平衡模式：采用非生成式天文后期，在真实约束下提升主体可见度、局部对比、"
            "背景洁净度和色彩表现，不调用生图模型。"
        )
    return _trim(f"{summary} 有效配置：{json.dumps(_jsonable(config), ensure_ascii=False)}", 1600)


def run(
    *,
    source_path: Path,
    output_dir: Path,
    result_path: Path,
    request_path: Path,
    inspection_path: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    request = _load_json(request_path)
    inspection = _load_json(inspection_path)
    style = str(request.get("style") or "realistic")
    if style not in {"realistic", "balanced"}:
        raise ValueError(f"run_starun_processing.py only supports realistic/balanced, got {style}")

    reference_artifact = _write_reference(source_path, output_dir)
    pipeline_result_path = output_dir / "pipeline-result.json"
    result_image_path = output_dir / "result.jpg"
    work_dir = output_dir / "intermediates"

    pipeline_result = pipeline.run_pipeline(
        input_path=str(source_path),
        output_path=str(result_image_path),
        preset="adaptive",
        keep_all=True,
        work_dir=str(work_dir),
        use_starnet=True,
        style="natural" if style == "realistic" else "auto",
        style_strength=0.8 if style == "realistic" else 1.0,
        result_json=str(pipeline_result_path),
        quality_policy="advisory",
    )
    if not isinstance(pipeline_result, dict):
        pipeline_result = _load_json(pipeline_result_path)

    pipeline_status = str(pipeline_result.get("status") or "failed")
    if pipeline_status not in {"success", "partial_success", "review_required", "failed"}:
        pipeline_status = "review_required"
    if pipeline_status == "failed":
        _write_failure(result_path, "skill_command_failed", "Processing pipeline reported failed status.")
        return

    artifacts = [
        _artifact(reference_artifact),
        _artifact(result_image_path.name),
        _artifact(pipeline_result_path.name),
    ]

    if style == "balanced":
        style_prompt_path = output_dir / "style-prompt.json"
        _write_json(style_prompt_path, _style_prompt(inspection, pipeline_result))
        artifacts.insert(1, _artifact(style_prompt_path.name))

    width, height = _result_size(result_image_path)
    quality_gates = pipeline_result.get("quality_gates") or []
    warnings = pipeline_result.get("warnings") or []
    if not isinstance(quality_gates, list):
        quality_gates = []
    if not isinstance(warnings, list):
        warnings = []
    quality_gates = quality_gates[:32]
    warnings = warnings[:32]

    _write_json(
        result_path,
        {
            "schema_version": "starun.skill-result/v1",
            "status": "success",
            "provider": "deep-sky-processor",
            "model": "pipeline.py",
            "style": style,
            "reference_artifact": reference_artifact,
            "result_artifact": result_image_path.name,
            "target_summary": _target_summary(pipeline_result, inspection),
            "visible_subject": _visible_subject(pipeline_result),
            "art_direction_summary": _art_direction_summary(style, pipeline_result),
            "quality_score": _quality_score(pipeline_status, quality_gates),
            "result_width": width,
            "result_height": height,
            "provider_request_id": None,
            "pipeline_status": pipeline_status,
            "quality_gates": _jsonable(quality_gates),
            "warnings": _jsonable(warnings),
            "artifacts": artifacts,
        },
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Starun processing skill SDK entrypoint")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--request-json", default="input/request.json")
    parser.add_argument("--inspection-json", default="input/inspection.json")
    parser.add_argument("--schema-json", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result_path = _sandbox_path(args.result)
    try:
        run(
            source_path=_sandbox_path(args.source),
            output_dir=_sandbox_path(args.output_dir),
            result_path=result_path,
            request_path=_sandbox_path(args.request_json),
            inspection_path=_sandbox_path(args.inspection_json),
        )
    except ImportError as exc:
        _write_failure(result_path, "runtime_dependency_missing", str(exc), missing_dependencies=[str(exc)])
        return 1
    except FileNotFoundError as exc:
        _write_failure(result_path, "skill_output_missing", str(exc))
        return 1
    except Exception as exc:
        _write_failure(
            result_path,
            "skill_command_failed",
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}",
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
