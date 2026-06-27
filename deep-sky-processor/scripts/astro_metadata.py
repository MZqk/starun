#!/usr/bin/env python3
"""Astropy-backed astronomy metadata evidence for deep-sky processing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from fits_io import classify_filter


SCHEMA_VERSION = "1.0"
ASTRO_EXTENSIONS = {".fit", ".fits", ".fts"}
WCS_KEYS = {
    "CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2",
    "CD1_1", "CD1_2", "CD2_1", "CD2_2", "CDELT1", "CDELT2", "CROTA2",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _first_header_value(header: Any, keys: tuple[str, ...]) -> tuple[str | None, Any]:
    if header is None:
        return None, None
    for key in keys:
        value = header.get(key)
        if value not in (None, ""):
            return key, value
    return None, None


def _quantity_field(header: Any, keys: tuple[str, ...], unit: str, confidence: str = "high") -> dict[str, Any] | None:
    key, value = _first_header_value(header, keys)
    if key is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return {
            "raw": str(value),
            "unit": unit,
            "source_key": key,
            "confidence": "low",
            "warning": "value is not numeric",
        }
    return {
        "value": value,
        "unit": unit,
        "source_key": key,
        "confidence": confidence,
    }


def _time_field(header: Any, warnings: list[dict[str, str]]) -> dict[str, Any] | None:
    key, value = _first_header_value(header, ("DATE-OBS", "DATE_OBS"))
    if key is None:
        return None
    try:
        from astropy.time import Time

        parsed = Time(value, scale="utc")
        return {
            "value": parsed.isot,
            "scale": parsed.scale,
            "source_key": key,
            "confidence": "medium",
        }
    except Exception as exc:
        warnings.append(_warning("TIME_PARSE_FAILED", str(exc)))
        return {
            "raw": str(value),
            "scale": "unknown",
            "source_key": key,
            "confidence": "low",
        }


def _target_coordinates(header: Any, warnings: list[dict[str, str]]) -> dict[str, Any]:
    name_key, name = _first_header_value(header, ("OBJECT", "TARGNAME"))
    ra_key, ra = _first_header_value(header, ("OBJCTRA", "RA", "CRVAL1"))
    dec_key, dec = _first_header_value(header, ("OBJCTDEC", "DEC", "CRVAL2"))
    target: dict[str, Any] = {
        "name": str(name) if name not in (None, "") else "unknown",
        "source": "header" if name_key else "unavailable",
    }
    if ra is None or dec is None:
        return target
    target["raw_ra"] = _jsonable(ra)
    target["raw_dec"] = _jsonable(dec)
    try:
        import astropy.units as u
        from astropy.coordinates import SkyCoord

        if isinstance(ra, str) or isinstance(dec, str):
            coord = SkyCoord(str(ra), str(dec), unit=(u.hourangle, u.deg), frame="icrs")
        else:
            coord = SkyCoord(float(ra) * u.deg, float(dec) * u.deg, frame="icrs")
        target.update({
            "ra_deg": round(float(coord.icrs.ra.deg), 8),
            "dec_deg": round(float(coord.icrs.dec.deg), 8),
            "frame": "icrs",
            "ra_source_key": ra_key,
            "dec_source_key": dec_key,
        })
    except Exception as exc:
        warnings.append(_warning("COORDINATE_PARSE_FAILED", str(exc)))
    return target


def _fits_hdu_summary(path: Path, warnings: list[dict[str, str]]) -> tuple[dict[str, Any], Any | None]:
    from astropy.io import fits

    with fits.open(path, memmap=False) as hdul:
        selected_index = None
        selected_hdu = None
        for index, hdu in enumerate(hdul):
            data = getattr(hdu, "data", None)
            if data is not None and getattr(data, "ndim", 0) >= 2:
                selected_index = index
                selected_hdu = hdu
                break
        if selected_hdu is None:
            raise ValueError("No image HDU with at least 2 dimensions found")
        data = selected_hdu.data
        header = selected_hdu.header.copy()
        summary = {
            "hdu_index": selected_index,
            "hdu_name": selected_hdu.name,
            "shape": list(data.shape),
            "dtype": str(data.dtype),
            "bitpix": int(header.get("BITPIX", 0)),
            "has_bscale_bzero": "BSCALE" in header or "BZERO" in header,
            "selection_reason": "first_image_hdu",
        }
        if selected_index != 0:
            warnings.append(_warning("NON_PRIMARY_IMAGE_HDU_SELECTED", f"hdu_index={selected_index}"))
        return summary, header


def _wcs_summary(header: Any, image_shape: list[int], warnings: list[dict[str, str]]) -> dict[str, Any]:
    if header is None:
        return {"available": False, "reason": "no_header"}
    present = {key for key in WCS_KEYS if header.get(key) is not None}
    if not {"CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2"} <= present:
        return {"available": False, "present_keywords": sorted(present)}
    try:
        from astropy.wcs import WCS
        from astropy.wcs.utils import proj_plane_pixel_scales

        wcs = WCS(header)
        scales_deg = np.abs(proj_plane_pixel_scales(wcs.celestial))
        height, width = image_shape[-2], image_shape[-1]
        center_ra, center_dec = wcs.pixel_to_world_values((width - 1) / 2.0, (height - 1) / 2.0)
        return {
            "available": True,
            "center_ra_deg": round(float(center_ra), 8),
            "center_dec_deg": round(float(center_dec), 8),
            "pixel_scale_arcsec": round(float(np.mean(scales_deg) * 3600.0), 4),
            "field_width_deg": round(float(scales_deg[0] * width), 5),
            "field_height_deg": round(float(scales_deg[1] * height), 5),
            "present_keywords": sorted(present),
        }
    except Exception as exc:
        warnings.append(_warning("WCS_PARSE_FAILED", str(exc)))
        return {"available": False, "present_keywords": sorted(present), "reason": "parse_failed"}


def _capture_summary(header: Any, warnings: list[dict[str, str]]) -> dict[str, Any]:
    capture: dict[str, Any] = {}
    for name, keys, unit, confidence in (
        ("exposure", ("EXPTIME", "EXPOSURE", "EXPOSURETIME"), "s", "high"),
        ("gain", ("GAIN", "EGAIN", "CAMGAIN"), "camera_native", "medium"),
        ("sensor_temperature", ("CCD-TEMP", "CCD_TEMP", "SENSOR_TEMP", "SET-TEMP"), "deg_C", "medium"),
        ("bin_x", ("XBINNING", "BINX"), "px", "medium"),
        ("bin_y", ("YBINNING", "BINY"), "px", "medium"),
        ("pixel_size", ("XPIXSZ", "PIXSIZE", "PIXELSIZE"), "um", "medium"),
        ("focal_length", ("FOCALLEN", "FOCAL"), "mm", "medium"),
        ("aperture", ("APTDIA", "APERTURE"), "mm", "medium"),
    ):
        field = _quantity_field(header, keys, unit, confidence=confidence)
        if field:
            capture[name] = field
    date_obs = _time_field(header, warnings)
    if date_obs:
        capture["date_obs"] = date_obs
    filter_key, filter_value = _first_header_value(header, ("FILTER", "FILTERID", "FILTNAME"))
    if filter_key:
        capture["filter"] = {
            "raw": str(filter_value),
            **classify_filter(filter_value),
            "source_key": filter_key,
        }
    return capture


def _priors(capture: dict[str, Any], warnings: list[dict[str, str]]) -> dict[str, Any]:
    recommendations: list[str] = []
    parameter_hints: dict[str, Any] = {}
    exposure = capture.get("exposure", {}).get("value")
    if exposure is not None and exposure >= 300:
        recommendations.append("long_subexposure_protect_star_cores")
        parameter_hints["ghs_protect_strength_min"] = 0.65
    filter_class = capture.get("filter", {}).get("class")
    if filter_class in {"dual_band", "narrowband"}:
        recommendations.extend(["use_emission_color_mode", "preserve_emission_line_physics"])
    confidence = "high" if len(capture) >= 4 else ("medium" if capture else "low")
    return {
        "confidence": confidence,
        "recommendations": recommendations,
        "parameter_hints": parameter_hints,
        "warnings": warnings,
    }


def build_astro_evidence(image_path: str | Path, *, plate_solution: dict[str, Any] | None = None) -> dict[str, Any]:
    path = Path(image_path)
    warnings: list[dict[str, str]] = []
    evidence: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source": "unsupported",
        "input": str(path),
        "warnings": warnings,
        "network": {"used": False, "services": []},
    }
    if path.suffix.lower() not in ASTRO_EXTENSIONS:
        warnings.append(_warning("UNSUPPORTED_ASTRO_METADATA_FORMAT", path.suffix.lower()))
        evidence.update({
            "fits": None,
            "capture": {},
            "coordinates": {
                "target": {"name": "unknown", "source": "unavailable"},
                "wcs": {"available": False},
            },
            "priors": _priors({}, warnings),
        })
        return evidence

    evidence["source"] = "fits_header"
    fits_summary, header = _fits_hdu_summary(path, warnings)
    capture = _capture_summary(header, warnings)
    coordinates = {
        "target": _target_coordinates(header, warnings),
        "wcs": _wcs_summary(header, fits_summary["shape"], warnings),
    }
    if plate_solution:
        coordinates["plate_solution"] = plate_solution
    evidence.update({
        "fits": fits_summary,
        "capture": capture,
        "coordinates": coordinates,
        "priors": _priors(capture, warnings),
    })
    return _jsonable(evidence)


def write_astro_evidence(
    image_path: str | Path,
    output_path: str | Path,
    *,
    plate_solution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = build_astro_evidence(image_path, plate_solution=plate_solution)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Astropy evidence JSON")
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    write_astro_evidence(args.input, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
