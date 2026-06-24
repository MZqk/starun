#!/usr/bin/env python3
"""Compile measured diagnostics into auditable deep-sky processing advice."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from software_guidance import get_software_guidance
from target_guidance import get_target_guidance
from report_zh import (
    REQUIRED_INFO,
    localized_evidence,
    localized_guidance,
    localized_operation,
    localized_value,
)


SCHEMA_VERSION = "1.0"
VALID_SOFTWARE = {"generic", "siril", "pixinsight", "photoshop"}
STAR_SUBJECTS = {"globular_cluster", "open_cluster", "m45"}
BACKGROUND_SENSITIVE = {
    "emission_nebula", "dark_nebula", "reflection_nebula",
    "supernova_remnant", "wide_field",
}
NARROWBAND_TOKENS = ("ha", "h-alpha", "halpha", "oiii", "o3", "sii", "s2", "dual", "duo")

OPERATION_LABELS = {
    "calibrate_integrate": "校准、选帧与叠加",
    "crop_edges": "裁切无效边缘",
    "background_review": "背景与梯度处理",
    "color_calibration": "宽带色彩校准",
    "narrowband_mapping": "窄带通道映射",
    "linear_denoise": "线性阶段降噪",
    "star_shape_review": "星点形态诊断",
    "controlled_stretch": "受控非线性拉伸",
    "highlight_protection": "亮核与高光保护",
    "star_treatment": "星点处理",
    "final_export": "母版保存与最终导出",
}

DECISION_LABELS = {
    "recommend": "建议执行",
    "review": "确认后执行",
    "skip": "当前跳过",
}


SOFTWARE_MAP = {
    "generic": {
        "calibrate_integrate": "Calibrate, register, reject poor subframes, and integrate before post-processing.",
        "crop_edges": "Crop only invalid stacking borders.",
        "background_review": "Build a trial low-frequency background model and inspect the model before applying it.",
        "color_calibration": "Use catalog-constrained color calibration after plate solving.",
        "narrowband_mapping": "Document the measured channel-to-color mapping; calibrate stars separately when appropriate.",
        "linear_denoise": "Apply masked linear noise reduction conservatively.",
        "star_shape_review": "Inspect subframes and spatial star-shape maps before attempting cosmetic correction.",
        "controlled_stretch": "Stretch in small increments while protecting highlights and monitoring black point.",
        "highlight_protection": "Use a range/core mask or HDR technique to protect bright structures.",
        "star_treatment": "Use optional, target-safe star reduction only after the target is established.",
        "final_export": "Preserve a high-bit-depth master and export a color-managed display copy.",
    },
    "siril": {
        "calibrate_integrate": "Use Siril preprocessing, registration, sequence assessment, rejection maps, and stacking.",
        "crop_edges": "Use Crop on invalid registration borders before background sampling.",
        "background_review": "Use Background Extraction/RBF cautiously; inspect samples and the generated model.",
        "color_calibration": "Plate solve, then use Photometric Color Calibration for broadband RGB/OSC data.",
        "narrowband_mapping": "Use Pixel Math/channel composition with an explicitly documented narrowband mapping.",
        "linear_denoise": "Use linear-stage wavelet/noise-reduction tools with a protective mask.",
        "star_shape_review": "Compare individual frames, registration results, and center/corner stars.",
        "controlled_stretch": "Use GHS, Asinh, or Histogram Transformation incrementally.",
        "highlight_protection": "Use GHS symmetry/protection controls or a range mask for bright cores.",
        "star_treatment": "Use star processing only when stars are not the subject; inspect at 100%.",
        "final_export": "Save a 32-bit FITS master and export a color-managed 16-bit TIFF/display image.",
    },
    "pixinsight": {
        "calibrate_integrate": "Use WBPP/SubframeSelector, inspect rejection maps, and integrate only accepted frames.",
        "crop_edges": "Use DynamicCrop on invalid registration borders before DBE/SPCC.",
        "background_review": "Use DBE/ABE only after validating samples and the background model against real sky structure.",
        "color_calibration": "Solve the image and use SPCC with the actual camera/filter response if it is available.",
        "narrowband_mapping": "Use PixelMath/NarrowbandNormalization with a documented mapping; do not call it natural RGB.",
        "linear_denoise": "Use MLT/TGV or another linear method under a luminance/range mask.",
        "star_shape_review": "Use FWHMEccentricity/SubframeSelector and inspect spatial trends before deconvolution.",
        "controlled_stretch": "Transfer a checked STF to HistogramTransformation or use GHS incrementally.",
        "highlight_protection": "Use RangeSelection masks, HDRMultiscaleTransform, or a restrained GHS stretch.",
        "star_treatment": "Use StarNet/MorphologicalTransformation only when target-safe and artifact-free.",
        "final_export": "Keep a 32-bit XISF master and export a color-managed 16-bit TIFF/display image.",
    },
    "photoshop": {
        "calibrate_integrate": "Photoshop is not the correct tool for calibration, registration, or integration.",
        "crop_edges": "Crop only known invalid stacking borders; keep an untouched master layer.",
        "background_review": "Return to linear astronomy software for background modeling; do not clone or heal the sky.",
        "color_calibration": "Perform photometric color calibration before Photoshop; use adjustment layers only for finishing.",
        "narrowband_mapping": "Import an already documented narrowband composition; do not fabricate missing channels.",
        "linear_denoise": "Perform linear denoising before Photoshop; use masked finishing noise reduction only if needed.",
        "star_shape_review": "Diagnose tracking/optical causes outside Photoshop; do not paint or warp stars as a default fix.",
        "controlled_stretch": "Use reversible Curves adjustment layers in small increments on a 16-bit image.",
        "highlight_protection": "Use luminosity masks and reversible Curves to protect bright cores and star color.",
        "star_treatment": "Use masked, low-opacity star adjustments only when target-safe; inspect for black halos.",
        "final_export": "Keep a layered 16-bit master and export an embedded-profile display copy.",
    },
}


def _get(payload, path, default=None):
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _evidence(payload, path, interpretation):
    return {
        "path": path,
        "value": _get(payload, path),
        "interpretation": interpretation,
    }


def _context_evidence(path, value, interpretation):
    return {
        "path": f"user_context.{path}",
        "value": value,
        "interpretation": interpretation,
    }


def _operation(
    operation_id,
    decision,
    confidence,
    evidence,
    purpose,
    starting_point,
    adjust,
    acceptance,
    rollback,
    software,
    parameter_mode="qualitative",
    parameter_rules=None,
    cautions=None,
):
    software_guidance = get_software_guidance(software, operation_id)
    software_guidance["checkpoints"] = list(acceptance)
    software_guidance["failure_signs"] = list(rollback)
    return {
        "id": operation_id,
        "decision": decision,
        "confidence": confidence,
        "evidence": evidence,
        "purpose": purpose,
        "software_instruction": SOFTWARE_MAP[software][operation_id],
        "software_guidance": software_guidance,
        "parameter_mode": parameter_mode,
        "parameter_rules": parameter_rules or [],
        "starting_point": starting_point,
        "how_to_adjust": adjust,
        "acceptance_checks": acceptance,
        "rollback_conditions": rollback,
        "cautions": cautions or [],
    }


def _is_narrowband(analysis, filter_override=None):
    filter_name = str(filter_override or _get(analysis, "classification.filter") or "").lower()
    return any(token in filter_name for token in NARROWBAND_TOKENS)


def _postprocessing_ready(analysis):
    stage = _get(analysis, "classification.processing_stage", "unknown")
    return stage == "stacked_or_integrated"


def compile_advice(analysis, software="generic", target_type="unknown", target_name=None, filter_name=None):
    if software not in VALID_SOFTWARE:
        raise ValueError(f"Unsupported software: {software}")
    operations = []
    stage = _get(analysis, "classification.processing_stage", "unknown")
    role = _get(analysis, "classification.frame_role", "unknown")
    transfer = _get(analysis, "classification.transfer_state", "unknown")
    is_narrowband = _is_narrowband(analysis, filter_name)
    target_key = (target_name or "").strip().lower().replace(" ", "")
    target_is_star_subject = target_type in STAR_SUBJECTS or target_key == "m45"

    if role in {"dark", "flat", "bias"}:
        operations.append(_operation(
            "calibrate_integrate", "recommend", "high",
            [_evidence(analysis, "classification.frame_role", "The file is classified as a calibration frame")],
            "Use this frame in calibration rather than treating it as a post-processing target.",
            "Verify that exposure, temperature, gain, binning, and optical configuration match the light frames.",
            "Build a master only from a consistent sequence and inspect the master for contamination.",
            ["The master behaves as expected when applied to representative lights.", "No target-like structure is introduced."],
            ["Calibration increases gradients, amp glow, dust shadows, or fixed-pattern artifacts."],
            software,
        ))
        return _payload(analysis, operations, software, target_type, target_name, filter_name)

    if role == "light" and stage != "stacked_or_integrated":
        operations.append(_operation(
            "calibrate_integrate", "recommend", "high",
            [
                _evidence(analysis, "classification.frame_role", "The file is classified as a light frame"),
                _evidence(analysis, "classification.processing_stage", "No integration evidence is present"),
            ],
            "Avoid making final post-processing decisions from one unintegrated exposure.",
            "Calibrate and assess a sequence before registration and integration.",
            "Reject only frames with documented focus, tracking, cloud, or background defects.",
            ["The integrated master improves background noise and preserves star shape.", "Rejection maps contain artifacts rather than real signal."],
            ["Calibration or rejection removes real stars/target structure or worsens fixed-pattern noise."],
            software,
        ))
        return _payload(analysis, operations, software, target_type, target_name, filter_name)

    exact_min = float(_get(analysis, "statistics.exact_min_ratio", 0) or 0)
    near_min = float(_get(analysis, "statistics.near_min_ratio", 0) or 0)
    if max(exact_min, near_min) >= 0.01:
        operations.append(_operation(
            "crop_edges", "review", "medium",
            [
                _evidence(analysis, "statistics.exact_min_ratio", "A measurable fraction equals the image minimum"),
                _evidence(analysis, "statistics.near_min_ratio", "A measurable fraction lies near the image minimum"),
            ],
            "Remove only invalid registration borders before statistics and background sampling.",
            "Inspect all four edges and crop the smallest rectangle that removes invalid borders.",
            "Do not crop valid low-signal sky merely because it is dark.",
            ["No zero/invalid registration wedges remain.", "The intended framing and faint outer signal are preserved."],
            ["The crop removes target structure, mosaic overlap, diffraction features, or valid dark sky."],
            software,
        ))

    gradient = float(_get(analysis, "background.plane.magnitude_across_frame", 0) or 0)
    gradient_r2 = float(_get(analysis, "background.plane.r_squared", 0) or 0)
    corner_range = float(_get(analysis, "background.corner_median_range", 0) or 0)
    gradient_detected = gradient >= 0.08 and gradient_r2 >= 0.55
    gradient_confidence = "medium" if gradient_detected else "low"
    background_cautions = [
        "A numeric trend is not proof of removable background.",
        "Inspect the background model and difference image before accepting correction.",
    ]
    if target_type in BACKGROUND_SENSITIVE or is_narrowband:
        background_cautions.append("This target/filter can contain real large-scale signal that resembles a gradient.")
    operations.append(_operation(
        "background_review",
        "review" if gradient_detected else "skip",
        gradient_confidence,
        [
            _evidence(analysis, "background.plane.magnitude_across_frame", "Measured low-signal plane change across the frame"),
            _evidence(analysis, "background.plane.r_squared", "Fraction of sampled low-signal variance explained by the plane"),
            _evidence(analysis, "background.corner_median_range", "Independent corner-to-corner background spread"),
        ],
        "Determine whether a correctable low-frequency gradient remains without removing real sky structure.",
        (
            "Create a low-complexity trial model from verified empty-sky samples; do not apply it immediately."
            if gradient_detected else
            "Do not run background extraction by default; inspect the background preview for a target-independent trend."
        ),
        "Increase model complexity only when residuals show a coherent instrumental/sky gradient and the model remains free of target structure.",
        [
            "背景模型只包含平滑的非目标低频成分。",
            "四角差异改善，且没有黑坑或目标边缘断裂。",
            "已知星云、尘埃、IFN、星系外晕和暗弱细丝保持原有形态。",
        ],
        [
            "背景模型中出现弧形结构、尘埃带、星系外晕、IFN 或星云细丝。",
            "校正后出现暗坑、颜色断层或四角过度扣除。",
        ],
        software,
        cautions=background_cautions,
    ))

    channel_model = str(_get(analysis, "classification.channel_model", "unknown"))
    if is_narrowband:
        operations.append(_operation(
            "narrowband_mapping", "review", "medium",
            [
                (
                    _context_evidence("filter", filter_name, "The user supplied the filter/channel context")
                    if filter_name else
                    _evidence(analysis, "classification.filter", "Filter metadata suggests narrowband or dual-band acquisition")
                ),
                _evidence(analysis, "color.channel_p99_normalized", "Measured channel signal distribution when RGB channels exist"),
            ],
            "Choose a documented color mapping without claiming unmeasured natural RGB color.",
            "Identify which emission lines are physically present before assigning display colors.",
            "Balance channels according to measured signal quality and target intent; avoid forcing weak channels to equal strength.",
            ["The mapping is documented.", "Weak-channel noise is not promoted into false structure.", "Star treatment is handled separately when needed."],
            ["The result implies missing channels, creates electric single-color blocks, or turns noise into apparent emission."],
            software,
            cautions=["Do not apply broadband white-balance assumptions to nebular line emission."],
        ))
    elif channel_model == "rgb":
        has_wcs = bool(_get(analysis, "file.header.WCSAXES"))
        operations.append(_operation(
            "color_calibration", "recommend" if has_wcs else "review", "medium",
            [
                _evidence(analysis, "classification.channel_model", "The data contains three image channels"),
                _evidence(analysis, "file.header.WCSAXES", "WCS evidence determines whether catalog calibration is immediately available"),
                _evidence(analysis, "color.background_ratios_to_mean", "Measured background channel imbalance"),
            ],
            "Constrain broadband color using stars and instrument response rather than visual neutralization alone.",
            "Plate solve first if WCS is unavailable, then use catalog-based color calibration with the actual filter/camera profile.",
            "Treat residual spatial color variation separately from global calibration.",
            ["Unsaturated star colors are plausible across the field.", "Background chromatic gradients are reduced without neutralizing real emission."],
            ["Calibration fails plate solving, clips a channel, whitens colored stars, or destroys expected target color."],
            software,
            cautions=["Background channel imbalance is not by itself proof of a color cast."],
        ))

    noise_sigma = float(_get(analysis, "noise.background_noise_sigma_normalized", 0) or 0)
    noise_blocks = int(_get(analysis, "noise.block_count", 0) or 0)
    noise_decision = "review" if noise_blocks >= 4 and noise_sigma >= 0.01 else "skip"
    operations.append(_operation(
        "linear_denoise", noise_decision, "medium" if noise_decision == "review" else "low",
        [
            _evidence(analysis, "noise.background_noise_sigma_normalized", "Normalized high-pass MAD noise estimate"),
            _evidence(analysis, "noise.block_count", "Number of low-signal blocks supporting the estimate"),
            _evidence(analysis, "classification.transfer_state", "Linear-stage denoising depends on transfer state"),
        ],
        "Reduce statistically supported background noise before stretch while preserving faint signal.",
        (
            "Test a conservative masked linear denoise on a duplicate and compare at 100%."
            if noise_decision == "review" else
            "Skip denoising unless visual review shows objectionable noise or a comparable version proves improvement."
        ),
        "Increase strength only if background variance falls while small stars and coherent faint structures remain.",
        ["Background grain decreases without plastic texture.", "Small stars and faint filaments remain.", "No block boundaries or chroma blotches appear."],
        ["Weak stars disappear, filaments break, dust becomes smooth plastic, or correlated blocks appear."],
        software,
        cautions=["This metric is not physical SNR and cannot prove that faint structure is noise."],
    ))

    star_evidence = _get(analysis, "stars.evidence")
    if star_evidence == "measured":
        eccentricity = float(_get(analysis, "stars.eccentricity_p90", 0) or 0)
        operations.append(_operation(
            "star_shape_review", "review" if eccentricity >= 0.45 else "skip", "medium",
            [
                _evidence(analysis, "stars.usable_star_count", "Number of validated star-like samples"),
                _evidence(analysis, "stars.fwhm_major_median_px", "Moment-based median major-axis FWHM"),
                _evidence(analysis, "stars.eccentricity_p90", "Upper-tail star eccentricity"),
                _evidence(analysis, "stars.position_angle_median_deg", "Median orientation of measured candidates"),
            ],
            "Determine whether star shape needs acquisition/registration diagnosis before cosmetic processing.",
            "Inspect center, corners, and individual subframes; compare direction and severity spatially.",
            "Separate global tracking elongation from corner-dependent optical aberration or registration error.",
            ["The suspected cause is supported by spatial and subframe behavior.", "Any correction preserves stellar profiles and color."],
            ["A cosmetic operation creates round-looking but nonphysical stars, black halos, clipped cores, or lost doubles."],
            software,
            parameter_mode="evidence_bound",
            parameter_rules=[
                {
                    "rule": "Use measured FWHM only as a relative scale for masks and inspection apertures.",
                    "evidence_path": "stars.fwhm_major_median_px",
                }
            ],
            cautions=["Moment-based FWHM is not a full PSF fit or seeing measurement."],
        ))

    if _postprocessing_ready(analysis):
        operations.append(_operation(
            "controlled_stretch", "recommend", "medium",
            [
                _evidence(analysis, "classification.processing_stage", "The file is treated as post-processing-ready"),
                _evidence(analysis, "classification.transfer_state", "Transfer-state heuristic guides whether a stretch is appropriate"),
                _evidence(analysis, "clipping.shadow_ratio_le_0_001", "Normalized shadow-end occupancy"),
                _evidence(analysis, "clipping.highlight_ratio_ge_0_999", "Normalized highlight-end occupancy"),
            ],
            "Reveal faint signal while preserving black point, star color, and bright-core detail.",
            "Apply multiple small stretch increments on a duplicate rather than one aggressive transform.",
            "Stop increasing the stretch when background noise rises faster than coherent target structure.",
            ["The background is separated from black without a hard cutoff.", "Bright cores retain internal structure.", "Star colors remain visible."],
            ["Black clipping increases, bright cores become flat white, stars bloat sharply, or noise dominates faint structure."],
            software,
            cautions=["Robust-normalized clipping ratios are review indicators, not physical sensor saturation."],
        ))

    highlight_ratio = float(_get(analysis, "clipping.highlight_ratio_ge_0_999", 0) or 0)
    if highlight_ratio >= 0.002:
        operations.append(_operation(
            "highlight_protection", "review", "low",
            [
                _evidence(analysis, "clipping.highlight_ratio_ge_0_999", "Bright-end occupancy in the robust review mapping"),
                _evidence(analysis, "statistics.exact_max_ratio", "Pixels exactly equal to the original maximum"),
            ],
            "Check whether bright stars or target cores need local protection during stretch.",
            "Inspect the highlights preview and original numeric range before creating a soft range/core mask.",
            "Increase protection only around verified bright structures; keep transitions broad and natural.",
            ["Core structure remains visible.", "Mask transitions are invisible.", "Unsaturated star color is retained."],
            ["The protected area becomes gray, develops a hard HDR boundary, or differs visibly from surrounding structure."],
            software,
            cautions=["Do not label this sensor saturation without original ADU/bit-depth evidence."],
        ))

    if target_is_star_subject:
        operations.append(_operation(
            "star_treatment", "skip", "high",
            [
                _context_evidence("target_type", target_type, "The user identified a star-dominated target type"),
                _context_evidence("target_name", target_name, "The user supplied the target name"),
            ],
            "Preserve the stellar population because stars are the subject.",
            "Do not remove or globally shrink stars.",
            "Use only restrained color and core protection if required.",
            ["Cluster structure, star hierarchy, doubles, and color remain intact."],
            ["Stars disappear, become uniformly tiny, lose color, or develop dark rings."],
            software,
            cautions=["M45, globular clusters, and open clusters require explicit star preservation."],
        ))
    elif star_evidence == "measured":
        density = float(_get(analysis, "stars.density_per_megapixel", 0) or 0)
        operations.append(_operation(
            "star_treatment", "review" if density >= 80 else "skip", "low",
            [
                _evidence(analysis, "stars.density_per_megapixel", "Density of validated bright star-like samples"),
                _evidence(analysis, "stars.fwhm_major_median_px", "Relative star scale"),
            ],
            "Decide whether stars visually overpower the target after stretch.",
            "Judge the stretched image first; if needed, test a low-strength star mask adjustment.",
            "Scale masks relative to measured FWHM and reduce strength when small stars disappear.",
            ["Target readability improves while star hierarchy and color remain natural.", "No black halos or clipped cores appear."],
            ["Stars become uniformly artificial, small stars vanish, or nebular knots are mistaken for stars."],
            software,
            parameter_mode="evidence_bound",
            parameter_rules=[
                {
                    "rule": "Derive mask scale from measured FWHM; do not use a fixed pixel radius.",
                    "evidence_path": "stars.fwhm_major_median_px",
                }
            ],
        ))

    operations.append(_operation(
        "final_export", "recommend", "high",
        [_evidence(analysis, "file.format", "Input format informs master/export handling")],
        "Preserve processing latitude and produce a predictable display copy.",
        "Save a high-bit-depth master before resizing, output sharpening, and color-space conversion.",
        "Apply output sharpening only at final display size and embed the intended profile.",
        ["The master remains high bit depth.", "The display copy has an embedded profile and no new clipping or halos."],
        ["Export changes color unexpectedly, introduces banding, or clips shadows/highlights."],
        software,
    ))
    return _payload(analysis, operations, software, target_type, target_name, filter_name)


def _payload(analysis, operations, software, target_type, target_name, filter_name):
    required_info = []
    if _get(analysis, "classification.processing_stage") == "unknown":
        required_info.append("Confirm whether the file is a calibrated single frame or an integrated master.")
    if _get(analysis, "classification.transfer_state") in ("unknown", None):
        required_info.append("Confirm whether the image is linear or already stretched.")
    if target_type == "unknown":
        required_info.append("Provide the target type to activate target-specific safety rules.")
    if not (filter_name or _get(analysis, "classification.filter")):
        required_info.append("Provide the filter or channel acquisition details.")

    # Extract key analysis data for report rendering
    analysis_summary = {
        "statistics": {
            "shape": _get(analysis, "statistics.shape"),
            "min": _get(analysis, "statistics.min"),
            "max": _get(analysis, "statistics.max"),
            "mean": _get(analysis, "statistics.mean"),
            "median": _get(analysis, "statistics.median"),
            "p99": _get(analysis, "statistics.p99"),
            "p99_9": _get(analysis, "statistics.p99_9"),
        },
        "background": {
            "plane_magnitude": _get(analysis, "background.plane.magnitude_across_frame"),
            "r_squared": _get(analysis, "background.plane.r_squared"),
            "corner_ratio": _get(analysis, "background.corner_mean_over_center"),
            "corner_range": _get(analysis, "background.corner_median_range"),
        },
        "noise": {
            "sigma": _get(analysis, "noise.background_noise_sigma_normalized"),
            "block_count": _get(analysis, "noise.block_count"),
        },
        "color": {
            "channel_model": _get(analysis, "classification.channel_model"),
            "background_medians": _get(analysis, "color.background_medians_normalized"),
            "channel_p99": _get(analysis, "color.channel_p99_normalized"),
            "correlation": _get(analysis, "color.channel_correlation"),
        },
        "stars": {
            "usable_count": _get(analysis, "stars.usable_star_count"),
            "fwhm": _get(analysis, "stars.fwhm_major_median_px"),
            "eccentricity": _get(analysis, "stars.eccentricity_p90"),
            "density": _get(analysis, "stars.density_per_megapixel"),
        },
        "clipping": {
            "shadow": _get(analysis, "clipping.shadow_ratio_le_0_001"),
            "highlight": _get(analysis, "clipping.highlight_ratio_ge_0_999"),
            "exact_min": _get(analysis, "statistics.exact_min_ratio"),
            "near_min": _get(analysis, "statistics.near_min_ratio"),
        },
        "file": {
            "format": _get(analysis, "file.format"),
            "header": {k: v for k, v in (analysis.get("file", {}).get("header", {}) or {}).items()
                      if k in ["IMAGETYP", "FILTER", "EXPTIME", "NAXIS1", "NAXIS2", "NAXIS3", "XPIXSZ", "FOCALLEN", "INSTRUME", "OBJECT", "TELESCOP", "CCD-TEMP"]},
        },
        "classification": {
            "frame_role": _get(analysis, "classification.frame_role"),
            "processing_stage": _get(analysis, "classification.processing_stage"),
            "transfer_state": _get(analysis, "classification.transfer_state"),
            "channel_model": _get(analysis, "classification.channel_model"),
            "filter": _get(analysis, "classification.filter"),
            "object": _get(analysis, "classification.object"),
        },
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "source_analysis_schema": analysis.get("schema_version"),
        "source_analysis_json": analysis.get("analysis_json"),
        "context": {
            "software": software,
            "target_type": target_type,
            "target_name": target_name,
            "filter": filter_name or _get(analysis, "classification.filter"),
        },
        "operations": operations,
        "required_information": required_info,
        "analysis_summary": analysis_summary,
        "policy": {
            "exact_parameters_require_evidence": True,
            "background_correction_requires_visual_model_review": True,
            "all_recommended_or_review_operations_require_acceptance_and_rollback": True,
        },
    }


def validate_advice(advice):
    errors = []
    for index, operation in enumerate(advice.get("operations", [])):
        prefix = f"operations[{index}]({operation.get('id')})"
        if operation.get("decision") in {"recommend", "review"}:
            evidence = operation.get("evidence") or []
            if not evidence or any(not item.get("path") for item in evidence):
                errors.append(f"{prefix}: missing evidence paths")
            elif not any(item.get("value") is not None for item in evidence):
                errors.append(f"{prefix}: all evidence values are unavailable")
            if not operation.get("acceptance_checks"):
                errors.append(f"{prefix}: missing acceptance checks")
            if not operation.get("rollback_conditions"):
                errors.append(f"{prefix}: missing rollback conditions")
            guidance = operation.get("software_guidance") or {}
            for field in ("tools", "steps", "parameter_logic", "mask_strategy", "checkpoints", "failure_signs"):
                if not guidance.get(field):
                    errors.append(f"{prefix}: missing software_guidance.{field}")
        if operation.get("parameter_mode") == "evidence_bound":
            for rule in operation.get("parameter_rules", []):
                if not rule.get("evidence_path"):
                    errors.append(f"{prefix}: evidence-bound parameter rule lacks evidence_path")
        if operation.get("parameter_mode") == "exact":
            errors.append(f"{prefix}: exact parameter mode is not allowed")
    return errors


SIRIL_OPS = {"calibrate_integrate", "crop_edges", "background_review", "color_calibration", "narrowband_mapping", "controlled_stretch", "final_export"}
PI_OPS = {"background_review", "color_calibration", "narrowband_mapping", "linear_denoise", "star_shape_review", "controlled_stretch", "highlight_protection", "star_treatment", "final_export"}
PS_OPS = {"background_review", "color_calibration", "narrowband_mapping", "linear_denoise", "controlled_stretch", "highlight_protection", "star_treatment", "final_export"}

SOFTWARE_DESC = {
    "siril": "Siril 是天文摄影的入口工具，核心任务是从原始帧构建干净、可靠的叠加母版。",
    "pixinsight": "PixInsight 是线性阶段处理的核心引擎，承担降噪、色彩校准、拉伸和细节增强的主要任务。",
    "photoshop": "Photoshop 是非线性阶段的精修工具，负责最终调色、局部增强、星点处理和输出优化。",
}

SOFTWARE_FOCI = {
    "siril": "叠加、校准、预处理",
    "pixinsight": "降噪、色彩校准、拉伸、细节增强",
    "photoshop": "最终调色、局部增强、星点处理、输出优化",
}


def _render_overall_section(context, analysis, guidance, active_ids, skip_ops, advice):
    lines = []
    lines.append("## 1. 整体后期处理建议")
    lines.append("")

    lines.append("### 数据评估")
    cls = analysis.get("classification", {})
    lines.append(f"- 帧角色：{localized_value(cls.get('frame_role', 'unknown'))}")
    lines.append(f"- 处理阶段：{localized_value(cls.get('processing_stage', 'unknown'))}")
    lines.append(f"- 转移状态：{localized_value(cls.get('transfer_state', 'unknown'))}")
    lines.append(f"- 通道/滤镜模型：{localized_value(cls.get('channel_model', 'unknown'))} / {context.get('filter') or '未知'}")
    lines.append(f"- 目标类型：{localized_value(context.get('target_type', 'unknown'))}")
    if context.get("target_name"):
        lines.append(f"- 目标名称：{context['target_name']}")
    first_active = next(
        (op for op in advice.get("operations", []) if op.get("decision") in {"recommend", "review"}),
        None,
    )
    if first_active:
        lines.append(f"- 置信度：{localized_value(first_active.get('confidence'))}")
    lines.append(f"- 置信度与缺失信息：见文末补充信息")
    lines.append("")

    lines.append("### 测量文件事实")
    lines.append("")
    stats = analysis.get("statistics", {})
    if stats.get("shape"):
        lines.append(f"- 图像尺寸：{stats['shape']}")
    if stats.get("min") is not None:
        lines.append(f"- 最小值：{stats['min']:.6f}")
    if stats.get("max") is not None:
        lines.append(f"- 最大值：{stats['max']:.6f}")
    if stats.get("mean") is not None:
        lines.append(f"- 均值：{stats['mean']:.6f}")
    if stats.get("median") is not None:
        lines.append(f"- 中位数：{stats['median']:.6f}")
    if stats.get("p99") is not None:
        lines.append(f"- P99：{stats['p99']:.6f}")
    if stats.get("p99_9") is not None:
        lines.append(f"- P99.9：{stats['p99_9']:.6f}")

    bg = analysis.get("background", {})
    if bg.get("plane_magnitude") is not None:
        lines.append(f"- 背景平面幅度：{bg['plane_magnitude']:.6f}")
    if bg.get("r_squared") is not None:
        lines.append(f"- 背景平面 R²：{bg['r_squared']:.6f}")

    noise = analysis.get("noise", {})
    if noise.get("sigma") is not None:
        lines.append(f"- 背景噪声 sigma：{noise['sigma']:.6f}")

    color = analysis.get("color", {})
    if color.get("background_medians"):
        lines.append(f"- 背景 RGB 中值：{color['background_medians']}")
    if color.get("channel_p99"):
        lines.append(f"- 通道 P99：{color['channel_p99']}")

    stars = analysis.get("stars", {})
    if stars.get("usable_count") is not None:
        lines.append(f"- 可用星点数：{stars['usable_count']}")
    if stars.get("fwhm") is not None:
        lines.append(f"- 星点 FWHM：{stars['fwhm']:.2f} px")

    clip = analysis.get("clipping", {})
    if clip.get("highlight") is not None:
        lines.append(f"- 接近饱和比例：{clip['highlight']:.4%}")
    if clip.get("shadow") is not None:
        lines.append(f"- 阴影裁切比例：{clip['shadow']:.4%}")
    lines.append("")

    lines.append("### 视觉发现")
    lines.append("")
    lines.append("[基于预览图像的描述]")
    lines.append("")

    lines.append("### 处理目标与风险")
    lines.append("")
    lines.append("**目标：** 在保留真实天文信号的前提下，进行可控拉伸、色彩处理和细节增强，获得结构清晰、色彩自然、噪声受控的图像。")
    lines.append("")
    lines.append("**针对该天体类型的关键保护：**")
    for item in guidance["overall"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("**主要风险：**")
    for item in guidance["overall_risks"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("### 推荐操作顺序")
    lines.append("")
    if active_ids:
        lines.append(" → ".join(OPERATION_LABELS[item] for item in active_ids))
    else:
        lines.append("当前证据不足，无法建立可靠的后期处理顺序。")
    lines.append("")

    lines.append("### 问题表")
    lines.append("")
    lines.append("| 发现 | 证据 | 置信度 | 可能影响 | 需确认 |")
    lines.append("|---|---|---:|---|---|")
    for op in advice["operations"]:
        if op["decision"] in {"recommend", "review"}:
            ev_summary = " / ".join(f"{localized_evidence(e)}={localized_value(e.get('value'))}" for e in op["evidence"][:2])
            lines.append(f"| {OPERATION_LABELS[op['id']]} | {ev_summary} | {localized_value(op['confidence'])} | {localized_operation(op)['purpose'][:40]}... | 见详细步骤 |")
    lines.append("")

    if skip_ops:
        lines.append("### 当前不建议的操作")
        lines.append("")
        lines.append("| 操作 | 原因 |")
        lines.append("|---|---|")
        for op in skip_ops:
            text = localized_operation(op)
            lines.append(f"| {OPERATION_LABELS[op['id']]} | {text['purpose']} |")
        lines.append("")

    if advice["required_information"]:
        lines.append("### 补充信息需求")
        lines.append("")
        for item in advice["required_information"]:
            lines.append(f"- {REQUIRED_INFO.get(item, item)}")
        lines.append("")

    return lines


def _render_software_section(software, guidance, ops_by_id, active_ids):
    lines = []
    idx = 2 + ["siril", "pixinsight", "photoshop"].index(software)
    lines.append(f"## {idx}. {localized_value(software)} 软件的后期关键步骤")
    lines.append("")
    lines.append(f"> {SOFTWARE_DESC[software]} 针对该天体类型的重点关注：{SOFTWARE_FOCI[software]}。")
    lines.append("")

    lines.append(f"### 针对该天体类型的关键策略")
    lines.append("")
    for item in guidance[software]:
        lines.append(f"- {item}")
    lines.append("")

    if software == "siril":
        relevant = [op_id for op_id in active_ids if op_id in SIRIL_OPS]
    elif software == "pixinsight":
        relevant = [op_id for op_id in active_ids if op_id in PI_OPS]
    else:
        relevant = [op_id for op_id in active_ids if op_id in PS_OPS]

    if relevant:
        lines.append(f"### {localized_value(software)} 中的详细操作指引")
        lines.append("")
        for op_id in relevant:
            op = ops_by_id[op_id]
            text = localized_operation(op)
            sw_guidance = get_software_guidance(software, op_id)

            lines.append(f"#### {OPERATION_LABELS[op_id]}")
            lines.append("")
            lines.append(f"- 目的：{text['purpose']}")
            lines.append(f"- 起始策略：{text['start']}")
            lines.append(f"- 调整原则：{text['adjust']}")
            lines.append("- 诊断证据：")
            for ev in op["evidence"]:
                lines.append(f"  - `{ev['path']}` = `{localized_value(ev.get('value'))}` — {localized_evidence(ev)}")
            lines.append(f"- 软件处理方向：{SOFTWARE_MAP[software][op_id]}")
            lines.append("- 关键工具：")
            for item in sw_guidance["tools"]:
                lines.append(f"  - {item}")
            lines.append("- 操作步骤：")
            for i, item in enumerate(sw_guidance["steps"], start=1):
                lines.append(f"  {i}. {item}")
            lines.append("- 参数选择依据：")
            for item in sw_guidance["parameter_logic"]:
                lines.append(f"  - {item}")
            lines.append("- 蒙版与保护策略：")
            for item in sw_guidance["mask_strategy"]:
                lines.append(f"  - {item}")
            lines.append("- 阶段验收：")
            op_guidance = op.get("software_guidance", {})
            for item in op_guidance.get("checkpoints", []):
                lines.append(f"  - {item}")
            lines.append("- 失败征象与回退：")
            for item in op_guidance.get("failure_signs", []):
                lines.append(f"  - {item}")
            lines.append("")

    return lines


def render_markdown(advice):
    context = advice["context"]
    target_type = context.get("target_type", "unknown")
    operations = advice.get("operations", [])
    analysis = advice.get("analysis_summary", {})
    guidance = get_target_guidance(target_type)

    ops_by_id = {op["id"]: op for op in operations}
    active_ids = [op["id"] for op in operations if op["decision"] in {"recommend", "review"}]
    skip_ops = [op for op in operations if op["decision"] == "skip"]

    lines = ["# 深空天体后期处理建议", ""]
    lines.extend(_render_overall_section(context, analysis, guidance, active_ids, skip_ops, advice))
    lines.extend(_render_software_section("siril", guidance, ops_by_id, active_ids))
    lines.extend(_render_software_section("pixinsight", guidance, ops_by_id, active_ids))
    lines.extend(_render_software_section("photoshop", guidance, ops_by_id, active_ids))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compile analysis JSON into auditable processing advice")
    parser.add_argument("analysis_json")
    parser.add_argument("--software", choices=sorted(VALID_SOFTWARE), default="generic")
    parser.add_argument("--target-type", default="unknown")
    parser.add_argument("--target-name")
    parser.add_argument("--filter")
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    args = parser.parse_args(argv)

    try:
        analysis_path = Path(args.analysis_json).expanduser().resolve()
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        advice = compile_advice(
            analysis,
            software=args.software,
            target_type=args.target_type,
            target_name=args.target_name,
            filter_name=args.filter,
        )
        errors = validate_advice(advice)
        if errors:
            raise ValueError("; ".join(errors))
        output_json = Path(args.output_json).expanduser().resolve() if args.output_json else analysis_path.with_name(
            analysis_path.stem.replace("_analysis", "") + "_advice.json"
        )
        output_markdown = Path(args.output_markdown).expanduser().resolve() if args.output_markdown else analysis_path.with_name(
            analysis_path.stem.replace("_analysis", "") + "_processing_report.md"
        )
        advice["advice_json"] = str(output_json)
        advice["report_markdown"] = str(output_markdown)
        output_json.write_text(json.dumps(advice, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output_markdown.write_text(render_markdown(advice), encoding="utf-8")
    except Exception as exc:
        print(f"Advice generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Advice JSON: {output_json}")
    print(f"Markdown report: {output_markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
