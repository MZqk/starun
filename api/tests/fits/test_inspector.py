import json
from pathlib import Path
from typing import Any
from unittest.mock import PropertyMock

import numpy as np
import pytest
from astropy.io import fits

from app.fits.errors import FitsStatisticsError, InvalidFitsError, UnsupportedFitsDataError
from app.fits.inspector import (
    CHUNK_TARGET_BYTES,
    HEADER_ENTRY_LIMIT,
    HEADER_SCAN_LIMIT,
    HEADER_TOTAL_BYTES_LIMIT,
    HEADER_VALUE_LENGTH_LIMIT,
    MEDIAN_SAMPLE_LIMIT,
    WORKING_BYTES_PER_ELEMENT,
    _bounded_raw_chunks,
    _chunk_length,
    _is_supported_image,
    _max_chunk_elements,
    _summarize_hdu,
    _safe_header,
    inspect_fits,
)
from app.fits.schemas import FitsInspection
from tests.fixtures.fits_factory import (
    make_blank_scaled_fits,
    make_compressed_only_fits,
    make_corrupt_fits,
    make_fits,
    make_rgb_fits,
    make_scaled_fits,
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


def test_compressed_image_is_listed_without_decompression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make_compressed_only_fits(tmp_path)
    data_guard = PropertyMock(side_effect=AssertionError("compressed data was decompressed"))
    monkeypatch.setattr(fits.CompImageHDU, "data", data_guard)

    with pytest.raises(UnsupportedFitsDataError):
        inspect_fits(path)

    assert data_guard.call_count == 0


def test_compressed_image_summary_uses_header_metadata_without_data_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hdu = object.__new__(fits.CompImageHDU)
    hdu.__dict__["_header"] = fits.Header(
        [
            ("XTENSION", "IMAGE"),
            ("BITPIX", 16),
            ("NAXIS", 2),
            ("NAXIS1", 9),
            ("NAXIS2", 7),
            ("EXTNAME", "COMPRESSED"),
        ]
    )
    data_guard = PropertyMock(side_effect=AssertionError("compressed data was decompressed"))
    monkeypatch.setattr(fits.CompImageHDU, "data", data_guard)

    summary = _summarize_hdu(1, hdu)

    assert summary.kind == "compressed_image"
    assert summary.shape == [7, 9]
    assert summary.dtype == "int16"
    assert summary.supported is False
    assert data_guard.call_count == 0


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


@pytest.mark.parametrize("shape", [(8,), (2, 3, 4, 5)])
def test_rejects_unsupported_image_dimensions(tmp_path: Path, shape: tuple[int, ...]) -> None:
    path = make_fits(
        tmp_path,
        np.zeros(shape, dtype=np.float32),
        name=f"{len(shape)}d.fits",
    )

    with pytest.raises(UnsupportedFitsDataError):
        inspect_fits(path)


@pytest.mark.parametrize(
    "dtype",
    [
        np.dtype(np.bool_),
        np.dtype(object),
        np.dtype("U4"),
        np.dtype("S4"),
    ],
)
def test_non_numeric_and_boolean_dtypes_are_not_supported(dtype: np.dtype[Any]) -> None:
    assert _is_supported_image([4, 4], dtype) is False


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


def test_non_default_bscale_and_bzero_produce_physical_statistics(tmp_path: Path) -> None:
    path, raw_data = make_scaled_fits(tmp_path)
    physical = raw_data.astype(np.float64) * 2.5 - 10.0

    statistics = inspect_fits(path).statistics

    assert statistics.minimum == float(np.min(physical))
    assert statistics.maximum == float(np.max(physical))
    assert statistics.mean == pytest.approx(float(np.mean(physical)))
    assert statistics.median == pytest.approx(float(np.median(physical)))
    assert statistics.standard_deviation == pytest.approx(float(np.std(physical)))


def test_blank_is_excluded_before_scaling(tmp_path: Path) -> None:
    path, raw_data, blank = make_blank_scaled_fits(tmp_path)
    physical = raw_data[raw_data != blank].astype(np.float64) * 2.5 - 10.0

    statistics = inspect_fits(path).statistics

    assert statistics.finite_pixel_count == 3
    assert statistics.minimum == float(np.min(physical))
    assert statistics.maximum == float(np.max(physical))
    assert statistics.mean == pytest.approx(float(np.mean(physical)))
    assert statistics.median == pytest.approx(float(np.median(physical)))
    assert statistics.standard_deviation == pytest.approx(float(np.std(physical)))


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


def test_header_bounds_scan_entries_values_duplicates_and_total_bytes() -> None:
    header = fits.Header()
    header.append(("DUPLIC", "first"))
    header.append(("DUPLIC", "second"))
    header["LONG"] = "x" * (HEADER_VALUE_LENGTH_LIMIT * 2)
    for index in range(HEADER_ENTRY_LIMIT * 2):
        header.append((f"V{index:07d}", "y" * HEADER_VALUE_LENGTH_LIMIT))

    safe = _safe_header(header)
    serialized = json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    assert safe["DUPLIC"] == "second"
    assert len(safe["LONG"]) == HEADER_VALUE_LENGTH_LIMIT
    assert len(safe) <= HEADER_ENTRY_LIMIT
    assert len(serialized) <= HEADER_TOTAL_BYTES_LIMIT


def test_header_scan_is_capped_before_late_cards() -> None:
    class SyntheticHeader:
        cards = [
            fits.Card("COMMENT", f"ignored {index}") for index in range(HEADER_SCAN_LIMIT)
        ] + [fits.Card("BEYOND", "must not be scanned")]

    assert "BEYOND" not in _safe_header(SyntheticHeader())


def test_opens_with_lazy_memmap_and_converts_only_bounded_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.fits import inspector

    path = make_fits(tmp_path, np.arange(128, dtype=np.float32).reshape(32, 4))
    real_open = fits.open
    open_kwargs: dict[str, Any] = {}
    converted_sizes: list[int] = []
    real_array = np.array

    def recording_open(*args: Any, **kwargs: Any) -> fits.HDUList:
        open_kwargs.update(kwargs)
        return real_open(*args, **kwargs)

    def recording_array(value: Any, *args: Any, **kwargs: Any) -> np.ndarray:
        dtype = kwargs.get("dtype", args[0] if args else None)
        if dtype is not None and np.dtype(dtype) == np.dtype(np.float64):
            converted_sizes.append(int(np.asarray(value).nbytes))
            assert kwargs.get("copy") is True
        return real_array(value, *args, **kwargs)

    monkeypatch.setattr(inspector, "CHUNK_TARGET_BYTES", 64)
    monkeypatch.setattr(inspector.fits, "open", recording_open)
    monkeypatch.setattr(inspector.np, "array", recording_array)

    inspect_fits(path)

    assert open_kwargs == {
        "memmap": True,
        "lazy_load_hdus": True,
        "do_not_scale_image_data": True,
    }
    assert converted_sizes
    assert max(converted_sizes) <= 64
    assert np.prod((32, 4)) * np.dtype(np.float32).itemsize > max(converted_sizes)


def test_raw_chunks_stay_bounded_when_one_row_exceeds_target() -> None:
    class SyntheticChunk:
        def __init__(self, nbytes: int) -> None:
            self.nbytes = nbytes

    class SyntheticArray:
        shape = (2, CHUNK_TARGET_BYTES // np.dtype(np.float32).itemsize + 1)
        dtype = np.dtype(np.float32)

        def __init__(self) -> None:
            self.keys: list[tuple[int | slice, ...]] = []

        def __getitem__(self, key: tuple[int | slice, ...]) -> SyntheticChunk:
            self.keys.append(key)
            row_key, column_key = key
            assert isinstance(row_key, int)
            assert isinstance(column_key, slice)
            width = int(column_key.stop) - int(column_key.start)
            return SyntheticChunk(width * self.dtype.itemsize)

    data = SyntheticArray()

    chunks = list(_bounded_raw_chunks(data))

    assert chunks
    assert max(chunk.nbytes for chunk in chunks) <= CHUNK_TARGET_BYTES
    assert len(data.keys) > 4
    assert all(isinstance(key[0], int) for key in data.keys)


@pytest.mark.parametrize("dtype", [np.dtype(np.uint8), np.dtype(np.float64)])
def test_transformed_working_set_is_bounded(dtype: np.dtype[Any]) -> None:
    max_elements = _max_chunk_elements(dtype)

    assert max_elements * WORKING_BYTES_PER_ELEMENT <= CHUNK_TARGET_BYTES
    assert (max_elements + 1) * WORKING_BYTES_PER_ELEMENT > CHUNK_TARGET_BYTES
    assert max_elements * dtype.itemsize <= CHUNK_TARGET_BYTES


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
