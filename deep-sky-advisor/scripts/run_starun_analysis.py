#!/usr/bin/env python3
"""Run Deep Sky Advisor as a Starun Agents SDK skill entrypoint."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import analyze_file
import generate_advice


def _workspace_root():
    candidates = [Path.cwd(), SCRIPT_DIR, *Path.cwd().parents, *SCRIPT_DIR.parents]
    for candidate in candidates:
        if (candidate / "input" / "request.json").is_file() or (candidate / "input").is_dir():
            return candidate
    return Path.cwd()


def _sandbox_path(value):
    path = Path(value)
    if path.is_absolute() or path.exists():
        return path
    return _workspace_root() / path


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get(payload, path, default=None):
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _trim(text, limit):
    text = " ".join(str(text).split())
    return text[:limit].rstrip() if len(text) > limit else text


def _rating(analysis):
    shadow = _as_float(_get(analysis, "clipping.shadow_ratio_le_0_001"))
    highlight = _as_float(_get(analysis, "clipping.highlight_ratio_ge_0_999"))
    noise = _as_float(_get(analysis, "noise.background_noise_sigma_normalized"))
    gradient = _as_float(_get(analysis, "background.plane.magnitude_across_frame"))
    eccentricity = _as_float(_get(analysis, "stars.eccentricity_p90"), 0.0)
    score = 1.0
    score -= min(0.3, shadow * 8)
    score -= min(0.25, highlight * 8)
    score -= min(0.25, max(0.0, noise - 0.01) * 6)
    score -= min(0.2, max(0.0, gradient - 0.04))
    score -= min(0.15, max(0.0, eccentricity - 0.45))
    if score >= 0.82:
        return "excellent", score
    if score >= 0.62:
        return "good", score
    if score >= 0.42:
        return "fair", score
    return "poor", score


def _stage_text(analysis):
    classification = analysis.get("classification", {})
    return (
        f"帧角色：{classification.get('frame_role', 'unknown')}；"
        f"处理阶段：{classification.get('processing_stage', 'unknown')}；"
        f"转移状态：{classification.get('transfer_state', 'unknown')}；"
        f"通道模型：{classification.get('channel_model', 'unknown')}。"
    )


def _professional_analysis(analysis, advice):
    rating, confidence = _rating(analysis)
    target_name = (
        _get(analysis, "classification.object")
        or _get(analysis, "file.header.OBJECT")
        or "未识别目标"
    )
    target_type = _get(advice, "context.target_type", "unknown")
    gradient = _as_float(_get(analysis, "background.plane.magnitude_across_frame"))
    r_squared = _as_float(_get(analysis, "background.plane.r_squared"))
    noise = _as_float(_get(analysis, "noise.background_noise_sigma_normalized"))
    stars = analysis.get("stars", {})
    color_model = _get(analysis, "classification.channel_model", "unknown")

    operations = [
        op for op in advice.get("operations", [])
        if op.get("decision") in {"recommend", "review"}
    ]
    operation_names = [
        generate_advice.OPERATION_LABELS.get(op.get("id"), op.get("id", "操作"))
        for op in operations
    ]

    issues = []
    if gradient >= 0.08:
        issues.append({
            "title": "背景梯度需要复核",
            "severity": "medium" if gradient < 0.16 else "high",
            "evidence": _trim(f"背景平面跨画幅幅度约 {gradient:.3f}，拟合解释度约 {r_squared:.2f}。", 1000),
            "recommendation": "先检查背景模型是否只包含平滑天光或光害残差，再决定是否执行背景提取。",
        })
    shadow = _as_float(_get(analysis, "clipping.shadow_ratio_le_0_001"))
    highlight = _as_float(_get(analysis, "clipping.highlight_ratio_ge_0_999"))
    if shadow > 0.01 or highlight > 0.01:
        issues.append({
            "title": "可能存在裁切或高光压缩风险",
            "severity": "medium",
            "evidence": _trim(f"阴影近零比例约 {shadow:.4f}，高光近满比例约 {highlight:.4f}。", 1000),
            "recommendation": "拉伸前检查直方图端点，避免继续压黑背景或压平亮星和核心。",
        })
    eccentricity = _as_float(stars.get("eccentricity_p90"))
    if eccentricity >= 0.55:
        issues.append({
            "title": "星点形态需要诊断",
            "severity": "medium",
            "evidence": _trim(f"星点 P90 偏心率约 {eccentricity:.2f}。", 1000),
            "recommendation": "优先检查导星、倾斜、场曲和子帧质量，不要默认用后期变形修复。",
        })
    if noise >= 0.03:
        issues.append({
            "title": "背景噪声偏高",
            "severity": "medium",
            "evidence": _trim(f"归一化背景噪声估计约 {noise:.3f}。", 1000),
            "recommendation": "在线性阶段使用蒙版保护目标结构和星点，进行保守降噪。",
        })
    issues = issues[:12]

    workflow = [
        {
            "order": 1,
            "step": "深空天体后期处理建议",
            "purpose": "先确认数据阶段、目标类型和需要保护的真实天文信号。",
            "guidance": _trim("建议顺序：" + ("、".join(operation_names) if operation_names else "补充数据阶段和目标类型后再制定流程。"), 1200),
        },
        {
            "order": 2,
            "step": "Siril 软件的后期关键步骤",
            "purpose": "完成校准、配准、叠加和初步背景审查，建立干净母版。",
            "guidance": "在 Siril 中优先检查序列质量、叠加边缘、背景样本和导出母版；不要把未叠加单帧当作最终后期对象。",
        },
        {
            "order": 3,
            "step": "PixInsight 软件的后期关键步骤",
            "purpose": "在线性阶段完成背景、校色、降噪、受控拉伸和细节保护。",
            "guidance": "在 PixInsight 中使用可复核的背景模型、SPCC/PCC、蒙版降噪和受控拉伸，保护弱信号、亮核和星色。",
        },
        {
            "order": 4,
            "step": "Photoshop 软件的后期关键步骤",
            "purpose": "在非线性阶段进行最终调色、局部增强、星点微调和输出优化。",
            "guidance": "在 Photoshop 中只处理已校准和高位深导出的图像，使用可逆调整图层、亮度蒙版和嵌入色彩配置的输出流程。",
        },
    ]

    return {
        "overview": _trim(
            f"{target_name} 的分析结果显示：{_stage_text(analysis)}"
            f"当前建议按证据执行可回退的后期流程，目标类型为 {target_type}。",
            2000,
        ),
        "image_quality": {
            "rating": rating,
            "summary": _trim(
                f"背景噪声估计 {noise:.3f}，背景梯度幅度 {gradient:.3f}，"
                f"星点可用样本 {stars.get('usable_star_count', 'unknown')}，通道模型 {color_model}。",
                1200,
            ),
            "confidence": max(0.0, min(1.0, confidence)),
        },
        "observations": {
            "target": _trim(f"目标/文件标识为 {target_name}，类型为 {target_type}；目标类型仍需结合拍摄信息确认。", 1200),
            "background": _trim(f"背景平面幅度约 {gradient:.3f}，该指标只能提示低频趋势，不能单独证明应移除背景。", 1200),
            "stars": _trim(
                f"检测到可用星点 {stars.get('usable_star_count', 'unknown')}；FWHM 诊断值为 {stars.get('fwhm_major_median_px', 'unknown')} 像素，偏心率 P90 为 {stars.get('eccentricity_p90', 'unknown')}。",
                1200,
            ),
            "noise": _trim(f"背景噪声归一化估计约 {noise:.3f}，不是物理 SNR。", 1200),
            "color": _trim(f"通道模型为 {color_model}，滤镜/通道信息为 {_get(analysis, 'classification.filter', 'unknown')}。", 1200),
        },
        "issues": issues,
        "workflow": workflow,
        "caveats": [
            "预览图经过显示拉伸，仅用于视觉诊断，不代表线性原始数据或最终成片。",
            "背景趋势、星点 FWHM 和噪声估计都是诊断量，不应作为固定软件预设直接套用。",
            "缺少 plate solving、测光校色验证和区域物理 SNR 时，所有目标类型判断都应保留不确定性。",
        ],
        "preview_metadata": {},
    }


def _write_failure(result_path, error_code, message, missing_dependencies=None):
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": "starun.skill-result/v1",
        "status": "failed",
        "error_code": error_code,
        "message": _trim(message, 1000),
        "retryable": False,
        "missing_dependencies": missing_dependencies or [],
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(source_path, output_dir, result_path, request_path=None):
    output_dir.mkdir(parents=True, exist_ok=True)
    analysis = analyze_file.analyze_image_file(source_path, output_dir)
    target_name = _get(analysis, "classification.object") or _get(analysis, "file.header.OBJECT")
    filter_name = _get(analysis, "classification.filter")
    advice = generate_advice.compile_advice(
        analysis,
        software="generic",
        target_type="unknown",
        target_name=target_name,
        filter_name=filter_name,
    )
    errors = generate_advice.validate_advice(advice)
    if errors:
        raise ValueError("; ".join(errors))

    source_preview = Path(analysis["previews"]["full"])
    preview_path = output_dir / "analysis-preview.png"
    if source_preview.resolve() != preview_path.resolve():
        shutil.copyfile(source_preview, preview_path)

    report_path = output_dir / "analysis-report.json"
    markdown_path = output_dir / "analysis-processing-report.md"
    markdown = generate_advice.render_markdown(advice)
    report = {
        "schema_version": "starun.analysis-report/v1",
        "source_analysis": analysis,
        "advice": advice,
        "markdown": markdown,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")

    with Image.open(preview_path) as image:
        width, height = image.size

    professional = _professional_analysis(analysis, advice)
    professional["preview_metadata"] = {
        "width": width,
        "height": height,
        "lower_percentile_value": 0.0,
        "upper_percentile_value": 1.0,
    }

    result = {
        "schema_version": "starun.skill-result/v1",
        "status": "success",
        "provider": "deep-sky-advisor",
        "model": "deterministic-skill-v1",
        "preview": {
            "artifact": preview_path.name,
            "width": width,
            "height": height,
            "lower_percentile_value": 0.0,
            "upper_percentile_value": 1.0,
        },
        "analysis": professional,
        "artifacts": [
            {"name": report_path.name, "media_type": "application/json"},
            {"name": preview_path.name, "media_type": "image/png"},
        ],
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Deep Sky Advisor and write a Starun skill result")
    parser.add_argument("--source", default="input/source.fits")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--result", default="output/analysis-result.json")
    parser.add_argument("--request-json", default="input/request.json")
    parser.add_argument("--schema-json", default="input/result-schema.json")
    args = parser.parse_args(argv)

    source_path = _sandbox_path(args.source)
    output_dir = _sandbox_path(args.output_dir)
    result_path = _sandbox_path(args.result)
    try:
        run(source_path, output_dir, result_path, Path(args.request_json))
    except ModuleNotFoundError as exc:
        missing = [exc.name] if exc.name else []
        _write_failure(result_path, "runtime_dependency_missing", str(exc), missing)
        return 1
    except Exception as exc:
        _write_failure(result_path, "skill_command_failed", str(exc))
        return 1
    print(f"Starun analysis result: {result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
