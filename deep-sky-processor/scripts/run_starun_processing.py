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
import analyze
import recognize
import astro_metadata


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


def _write_analysis_report(source_path: Path, output_dir: Path) -> dict[str, Any] | None:
    """Generate the adaptive analysis report used by the Starun direct entrypoint."""
    report_path = output_dir / "analysis-report.json"
    try:
        report = analyze.analyze_image(str(source_path))
    except Exception as exc:
        print(f"[WARN] adaptive analysis failed: {exc}", file=sys.stderr)
        return None
    _write_json(report_path, _jsonable(report))
    return report


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"unknown", "none", "null"}:
            return text
    return None


def _infer_target_context(
    request: dict[str, Any],
    inspection: dict[str, Any],
    analysis_report: dict[str, Any] | None,
    astro_evidence: dict[str, Any] | None,
) -> dict[str, str | None]:
    """Infer target metadata for the compact Starun entrypoint.

    The standalone agent can visually review previews, but the Starun direct
    path must preserve the best local evidence so pipeline safety rules run.
    """
    analysis_report = analysis_report or {}
    astro_evidence = astro_evidence or {}

    request_target = request.get("target") if isinstance(request.get("target"), dict) else {}
    inspection_target = (
        inspection.get("target") if isinstance(inspection.get("target"), dict) else {}
    )
    evidence_target = (
        (astro_evidence.get("coordinates") or {}).get("target")
        if isinstance(astro_evidence.get("coordinates"), dict)
        else {}
    ) or {}
    capture = analysis_report.get("capture_metadata") or {}

    target_name = _first_text(
        request.get("target_name"),
        request.get("object"),
        request_target.get("name"),
        inspection.get("target_name"),
        inspection.get("object"),
        inspection_target.get("name"),
        evidence_target.get("name"),
        capture.get("object"),
    )

    target_type = _first_text(
        request.get("target_type"),
        request_target.get("type"),
        inspection.get("target_type"),
        inspection_target.get("type"),
    )
    if not target_type:
        hint = analysis_report.get("target_type_hint") or {}
        confidence = float(hint.get("confidence") or 0.0)
        hinted_type = _first_text(hint.get("target_type"))
        if hinted_type and confidence >= 0.35:
            target_type = hinted_type

    if target_name and not target_type:
        name_upper = target_name.upper().replace(" ", "")
        if any(
            marker in name_upper
            for marker in ("NGC6888", "CRESCENTNEBULA", "NGC7000", "NORTHAMERICANEBULA")
        ):
            target_type = "emission_nebula"

    color = analysis_report.get("color") or {}
    color_mode = _first_text(
        request.get("color_mode"),
        inspection.get("color_mode"),
        color.get("recommended_mode"),
    )
    if (
        target_type == "emission_nebula"
        or color.get("color_health_effective") == "emission_dominant"
        or color.get("signal_interpretation") == "expected_emission_dominance"
    ):
        color_mode = "emission"

    return {
        "target_type": target_type,
        "target_name": target_name,
        "color_mode": color_mode or "auto",
    }


def _starun_steps_for_context(target_type: str | None) -> str | None:
    if target_type != "emission_nebula":
        return None
    return ",".join(
        [
            "color",
            "pre_denoise",
            "star_remove",
            "stretch",
            "star_process",
            "final_color",
            "local_enhance",
            "style",
            "star_combine",
            "final_denoise",
        ]
    )


def _starun_overrides_for_context(
    style: str,
    target_type: str | None,
    analysis_report: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if target_type != "emission_nebula":
        return None

    report = analysis_report or {}
    gradient = report.get("gradient") or {}
    starfield = report.get("starfield") or {}
    overrides: dict[str, Any] = {
        "star_reduction": 0.25,
        "star_combine_strength": 0.50 if style == "realistic" else 0.58,
        "saturation": 0.78 if style == "realistic" else 0.92,
        "final_denoise_lum": 0.0025,
        "final_denoise_chroma": 0.005,
    }

    dbe_decision = gradient.get("dbe_decision")
    dbe_recommendation = gradient.get("dbe_method_recommendation")
    if (
        dbe_decision in {"skip", "review_chromatic"}
        or dbe_recommendation == "skip"
        or gradient.get("gradient_severity") == "none"
    ):
        overrides["dbe_method"] = "skip"

    if report.get("brightness", {}).get("very_dark_eligible"):
        overrides.setdefault("stretch_gamma", 0.46)
        overrides.setdefault("target_bg", 0.12 if style == "balanced" else 0.10)

    if starfield.get("star_density") in {"dense", "very_dense"}:
        overrides["star_combine_strength"] = min(
            overrides["star_combine_strength"],
            0.48 if style == "realistic" else 0.56,
        )

    return overrides


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
    analysis_report = _write_analysis_report(source_path, output_dir)
    astro_evidence_path = output_dir / "astro-evidence.json"
    try:
        astro_evidence = astro_metadata.write_astro_evidence(source_path, astro_evidence_path)
    except Exception as exc:
        astro_evidence = {
            "schema_version": "1.0",
            "warnings": [{"code": "ASTRO_EVIDENCE_FAILED", "message": str(exc)}],
        }
        _write_json(astro_evidence_path, astro_evidence)

    target_context = _infer_target_context(
        request,
        inspection,
        analysis_report,
        astro_evidence,
    )
    target_type = target_context["target_type"]
    target_name = target_context["target_name"]
    color_mode = target_context["color_mode"] or "auto"
    steps = _starun_steps_for_context(target_type)
    override_params = _starun_overrides_for_context(
        style,
        target_type,
        analysis_report,
    )
    local_strength = 0.10 if style == "realistic" else 0.16

    pipeline_result = pipeline.run_pipeline(
        input_path=str(source_path),
        output_path=str(result_image_path),
        steps=steps,
        preset="adaptive",
        keep_all=True,
        work_dir=str(work_dir),
        use_starnet=True,
        style="natural" if style == "realistic" else "auto",
        style_strength=0.8 if style == "realistic" else 1.0,
        target_type=target_type,
        target_name=target_name,
        color_mode=color_mode,
        override_params=override_params,
        local_strength=local_strength,
        analysis_report=analysis_report,
        astro_evidence=str(astro_evidence_path),
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
        _artifact(astro_evidence_path.name),
    ]
    if analysis_report is not None:
        artifacts.append(_artifact("analysis-report.json"))

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
