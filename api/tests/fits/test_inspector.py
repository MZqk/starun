import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from app.fits.errors import FitsStatisticsError, InvalidFitsError, UnsupportedFitsDataError
from app.fits.inspector import (
    CHUNK_TARGET_BYTES,
    MEDIAN_SAMPLE_LIMIT,
    _chunk_length,
    inspect_fits,
)
from app.fits.schemas import FitsInspection
from tests.fixtures.fits_factory import (
    make_corrupt_fits,
    make_fits,
    make_rgb_fits,
    make_table_only_fits,
    make_uint16_fits,
)


def test_selects_largest_supported_image_and_reports_zero_statistics(tmp_path: Path) -> None:
    path = make_fits(
        tmp_path,
        np.zeros((32, 32), dtype=np.float32),
        [np.zeros((64, 48), dtype=np.float32)],
    )

    inspection = inspect_fits(path)

    assert [hdu.kind for hdu in inspection.hdus] == ["primary_image", "image"]
    assert inspection.selected_hdu.index == 1
    assert inspection.selected_hdu.shape == [64, 48]
    assert inspection.statistics.model_dump() == {
        "minimum": 0.0,
        "maximum": 0.0,
        "mean": 0.0,
        "median": 0.0,
        "standard_deviation": 0.0,
        "finite_pixel_count": 64 * 48,
    }


def test_rejects_table_only_fits_after_listing_no_supported_images(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFitsDataError) as exc_info:
        inspect_fits(make_table_only_fits(tmp_path))

    assert exc_info.value.error_code == "unsupported_fits_data"
    assert str(exc_info.value) == "The FITS file does not contain a supported image."


def test_rejects_corrupt_bytes_with_safe_error(tmp_path: Path) -> None:
    with pytest.raises(InvalidFitsError) as exc_info:
        inspect_fits(make_corrupt_fits(tmp_path))

    assert exc_info.value.error_code == "invalid_fits"
    assert "corrupt.fits" not in str(exc_info.value)


@pytest.mark.parametrize("shape", [(3, 8, 5), (8, 5, 3)])
def test_accepts_unambiguous_three_channel_layouts(
    tmp_path: Path, shape: tuple[int, int, int]
) -> None:
    inspection = inspect_fits(make_rgb_fits(tmp_path, shape, name=f"rgb-{shape[0]}.fits"))

    assert inspection.selected_hdu.shape == list(shape)
    assert inspection.selected_hdu.supported is True


def test_rejects_ambiguous_three_channel_layout(tmp_path: Path) -> None:
    path = make_rgb_fits(tmp_path, (3, 8, 3), name="ambiguous.fits")

    with pytest.raises(UnsupportedFitsDataError):
        inspect_fits(path)


def test_equal_pixel_count_selects_lower_hdu_index(tmp_path: Path) -> None:
    path = make_fits(
        tmp_path,
        np.ones((6, 8), dtype=np.int16),
        [np.full((8, 6), 2, dtype=np.int16)],
    )

    inspection = inspect_fits(path)

    assert inspection.selected_hdu.index == 0
    assert inspection.statistics.mean == 1.0


def test_ignores_non_finite_pixels(tmp_path: Path) -> None:
    data = np.array([[1.0, np.nan], [np.inf, 3.0]], dtype=np.float32)

    statistics = inspect_fits(make_fits(tmp_path, data)).statistics

    assert statistics.finite_pixel_count == 2
    assert statistics.minimum == 1.0
    assert statistics.maximum == 3.0
    assert statistics.mean == 2.0
    assert statistics.median == 2.0
    assert statistics.standard_deviation == 1.0


def test_rejects_image_with_no_finite_pixels(tmp_path: Path) -> None:
    data = np.array([[np.nan, np.inf], [-np.inf, np.nan]], dtype=np.float32)

    with pytest.raises(FitsStatisticsError) as exc_info:
        inspect_fits(make_fits(tmp_path, data))

    assert exc_info.value.error_code == "fits_statistics_failed"


def test_uint16_scaling_produces_physical_statistics(tmp_path: Path) -> None:
    path, data = make_uint16_fits(tmp_path)

    statistics = inspect_fits(path).statistics

    assert statistics.minimum == 0.0
    assert statistics.maximum == 65535.0
    assert statistics.mean == pytest.approx(float(np.mean(data, dtype=np.float64)))
    assert statistics.median == pytest.approx(float(np.median(data)))
    assert statistics.standard_deviation == pytest.approx(float(np.std(data, dtype=np.float64)))


def test_header_is_selected_hdu_only_bounded_and_json_safe(tmp_path: Path) -> None:
    primary_header = fits.Header()
    primary_header["PRIMARY"] = "not selected"
    extension_header = fits.Header()
    extension_header["COMPLEX"] = complex(1, 2)
    extension_header["HISTORY"] = "internal history"
    extension_header["COMMENT"] = "internal comment"
    extension_header.append(("", "blank key"))
    for index in range(300):
        extension_header[f"K{index:06d}"] = index

    path = tmp_path / "headers.fits"
    fits.HDUList(
        [
            fits.PrimaryHDU(data=np.zeros((2, 2)), header=primary_header),
            fits.ImageHDU(data=np.zeros((4, 4)), header=extension_header),
        ]
    ).writeto(path)

    inspection = inspect_fits(path)

    assert len(inspection.header) == 256
    assert "PRIMARY" not in inspection.header
    assert "HISTORY" not in inspection.header
    assert "COMMENT" not in inspection.header
    assert "" not in inspection.header
    assert inspection.header["COMPLEX"] == "(1+2j)"
    json.loads(inspection.model_dump_json())


def test_opens_with_lazy_memmap_and_converts_only_bounded_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.fits import inspector

    path = make_fits(tmp_path, np.arange(128, dtype=np.float32).reshape(32, 4))
    real_open = fits.open
    open_kwargs: dict[str, Any] = {}
    converted_sizes: list[int] = []
    real_asarray = np.asarray

    def recording_open(*args: Any, **kwargs: Any) -> fits.HDUList:
        open_kwargs.update(kwargs)
        return real_open(*args, **kwargs)

    def recording_asarray(value: Any, *args: Any, **kwargs: Any) -> np.ndarray:
        dtype = kwargs.get("dtype", args[0] if args else None)
        if dtype is not None and np.dtype(dtype) == np.dtype(np.float64):
            converted_sizes.append(int(real_asarray(value).nbytes))
        return real_asarray(value, *args, **kwargs)

    monkeypatch.setattr(inspector, "CHUNK_TARGET_BYTES", 64)
    monkeypatch.setattr(inspector.fits, "open", recording_open)
    monkeypatch.setattr(inspector.np, "asarray", recording_asarray)

    inspect_fits(path)

    assert open_kwargs == {
        "memmap": True,
        "lazy_load_hdus": True,
        "do_not_scale_image_data": True,
    }
    assert converted_sizes
    assert max(converted_sizes) <= 64
    assert np.prod((32, 4)) * np.dtype(np.float32).itemsize > max(converted_sizes)


def test_median_sample_is_capped_and_even_for_large_logical_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.fits import inspector

    path = make_fits(tmp_path, np.arange(100, dtype=np.float32).reshape(10, 10))
    median_input_sizes: list[int] = []
    real_median = np.median

    def recording_median(values: np.ndarray) -> np.floating[Any]:
        median_input_sizes.append(values.size)
        return real_median(values)

    monkeypatch.setattr(inspector, "MEDIAN_SAMPLE_LIMIT", 10)
    monkeypatch.setattr(inspector.np, "median", recording_median)

    inspection = inspect_fits(path)

    assert median_input_sizes == [10]
    assert inspection.statistics.median == 49.5
    assert _chunk_length((1_000_000, 4096), np.dtype(np.float32)) == (
        CHUNK_TARGET_BYTES // (4096 * np.dtype(np.float32).itemsize)
    )
    assert MEDIAN_SAMPLE_LIMIT == 100_000


def test_median_schema_documents_bounded_estimate() -> None:
    description = FitsInspection.model_fields["statistics"].annotation.model_fields[
        "median"
    ].description

    assert description is not None
    assert "100,000" in description
