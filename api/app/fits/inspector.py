import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits  # type: ignore[import-untyped]

from app.fits.errors import (
    FitsInspectionError,
    FitsStatisticsError,
    InvalidFitsError,
    UnsupportedFitsDataError,
)
from app.fits.schemas import BasicStatistics, FitsInspection, HduSummary

CHUNK_TARGET_BYTES = 16 * 1024 * 1024
# float64 transformed values + boolean validity mask + float64 scratch/indices.
WORKING_BYTES_PER_ELEMENT = 17
MEDIAN_SAMPLE_LIMIT = 100_000
HEADER_ENTRY_LIMIT = 256
HEADER_SCAN_LIMIT = 512
HEADER_VALUE_LENGTH_LIMIT = 2048
HEADER_TOTAL_BYTES_LIMIT = 64 * 1024

_BITPIX_DTYPES: dict[int, np.dtype[Any]] = {
    8: np.dtype(np.uint8),
    16: np.dtype(np.int16),
    32: np.dtype(np.int32),
    64: np.dtype(np.int64),
    -32: np.dtype(np.float32),
    -64: np.dtype(np.float64),
}


def inspect_fits(path: Path) -> FitsInspection:
    """Inspect FITS image metadata and bounded-memory statistics.

    The median is exact for at most 100,000 finite pixels. Larger images use
    100,000 evenly spaced finite-pixel ranks as a deterministic estimate.
    """
    try:
        with fits.open(
            path,
            memmap=True,
            lazy_load_hdus=True,
            do_not_scale_image_data=True,
        ) as hdus:
            summaries = [_summarize_hdu(index, hdu) for index, hdu in enumerate(hdus)]
            supported = [summary for summary in summaries if summary.supported]
            if not supported:
                raise UnsupportedFitsDataError()

            selected = max(
                supported,
                key=lambda summary: (_pixel_count(summary.shape), -summary.index),
            )
            selected_hdu = hdus[selected.index]
            statistics = _calculate_statistics(selected_hdu)
            header = _safe_header(selected_hdu.header)
            return FitsInspection(
                hdus=summaries,
                selected_hdu=selected,
                statistics=statistics,
                header=header,
            )
    except FitsInspectionError:
        raise
    except (EOFError, OSError, TypeError, ValueError) as exc:
        raise InvalidFitsError() from exc


def _summarize_hdu(index: int, hdu: Any) -> HduSummary:
    kind = _hdu_kind(hdu)
    if kind == "compressed_image":
        shape, dtype = _compressed_image_metadata(hdu.header)
        supported = False
    elif kind in {"primary_image", "image"}:
        data = hdu.data
        if data is None:
            shape = None
            dtype = None
            supported = False
        else:
            shape = [int(length) for length in data.shape]
            array_dtype = np.dtype(data.dtype)
            dtype = array_dtype.name
            supported = _is_supported_image(shape, array_dtype)
    elif kind != "compressed_image":
        raw_shape = getattr(hdu, "shape", None)
        shape = [int(length) for length in raw_shape] if raw_shape else None
        dtype = None
        supported = False

    return HduSummary(
        index=index,
        name=str(getattr(hdu, "name", "") or ""),
        kind=kind,
        shape=shape,
        dtype=dtype,
        supported=supported,
    )


def _hdu_kind(hdu: Any) -> str:
    if isinstance(hdu, fits.PrimaryHDU):
        return "primary_image"
    if isinstance(hdu, fits.CompImageHDU):
        return "compressed_image"
    if isinstance(hdu, fits.BinTableHDU):
        return "binary_table"
    if isinstance(hdu, fits.TableHDU):
        return "ascii_table"
    if isinstance(hdu, fits.ImageHDU):
        return "image"
    return "unknown"


def _compressed_image_metadata(header: fits.Header) -> tuple[list[int] | None, str | None]:
    axis_count = header.get("NAXIS")
    bitpix = header.get("BITPIX")
    if not isinstance(axis_count, int) or axis_count < 0:
        shape = None
    else:
        axis_lengths = [header.get(f"NAXIS{axis}") for axis in range(1, axis_count + 1)]
        shape = (
            [int(length) for length in reversed(axis_lengths)]
            if all(isinstance(length, int) for length in axis_lengths)
            else None
        )
    dtype = _BITPIX_DTYPES.get(bitpix)
    return shape, dtype.name if dtype is not None else None


def _is_supported_image(shape: list[int], dtype: np.dtype[Any]) -> bool:
    is_boolean = np.issubdtype(dtype, np.bool_)
    is_integer = np.issubdtype(dtype, np.integer)
    is_floating = np.issubdtype(dtype, np.floating)
    if is_boolean or not (is_integer or is_floating):
        return False
    if len(shape) == 2:
        return True
    return len(shape) == 3 and sum(length == 3 for length in shape) == 1


def _pixel_count(shape: list[int] | None) -> int:
    if shape is None:
        return 0
    return int(np.prod(shape, dtype=np.int64))


def _chunk_length(shape: tuple[int, ...], dtype: np.dtype[Any]) -> int:
    if not shape:
        return 1
    values_per_slice = int(np.prod(shape[1:], dtype=np.int64))
    bytes_per_slice = max(1, values_per_slice * dtype.itemsize)
    return max(1, CHUNK_TARGET_BYTES // bytes_per_slice)


def _max_chunk_elements(dtype: np.dtype[Any]) -> int:
    raw_limit = CHUNK_TARGET_BYTES // max(1, dtype.itemsize)
    working_limit = CHUNK_TARGET_BYTES // WORKING_BYTES_PER_ELEMENT
    return max(1, min(raw_limit, working_limit))


def _bounded_raw_chunks(data: Any) -> Iterator[Any]:
    shape = tuple(int(length) for length in data.shape)
    dtype = np.dtype(data.dtype)
    max_elements = _max_chunk_elements(dtype)

    def chunks_at_axis(prefix: tuple[int | slice, ...], axis: int) -> Iterator[Any]:
        trailing_elements = int(np.prod(shape[axis + 1 :], dtype=np.int64))
        axis_chunk_length = max_elements // max(1, trailing_elements)
        if axis_chunk_length > 0:
            remaining_axes = (slice(None),) * (len(shape) - axis - 1)
            for start in range(0, shape[axis], axis_chunk_length):
                stop = min(shape[axis], start + axis_chunk_length)
                yield data[prefix + (slice(start, stop),) + remaining_axes]
            return

        for index in range(shape[axis]):
            yield from chunks_at_axis(prefix + (index,), axis + 1)

    yield from chunks_at_axis((), 0)


def _physical_chunks(
    hdu: Any,
) -> Iterator[tuple[np.ndarray[Any, np.dtype[np.float64]], np.ndarray[Any, np.dtype[np.bool_]]]]:
    data = hdu.data
    bscale = float(hdu.header.get("BSCALE", 1.0))
    bzero = float(hdu.header.get("BZERO", 0.0))
    blank = hdu.header.get("BLANK")

    for raw_chunk in _bounded_raw_chunks(data):
        values = np.array(raw_chunk, dtype=np.float64, copy=True)
        if blank is not None:
            valid = np.equal(raw_chunk, blank)
            values[valid] = np.nan
        if bscale != 1.0:
            values *= bscale
        if bzero != 0.0:
            values += bzero
        if blank is None:
            valid = np.empty(values.shape, dtype=np.bool_)
        np.isfinite(values, out=valid)
        yield values, valid


def _calculate_statistics(hdu: Any) -> BasicStatistics:
    try:
        count = 0
        mean = 0.0
        m2 = 0.0
        minimum = np.inf
        maximum = -np.inf

        for values, valid in _physical_chunks(hdu):
            chunk_count = int(np.count_nonzero(valid))
            if chunk_count == 0:
                continue

            chunk_sum = float(np.sum(values, where=valid, initial=0.0, dtype=np.float64))
            chunk_mean = chunk_sum / chunk_count
            squared_deviations = np.empty_like(values)
            np.subtract(values, chunk_mean, out=squared_deviations)
            np.square(squared_deviations, out=squared_deviations)
            chunk_m2 = float(
                np.sum(squared_deviations, where=valid, initial=0.0, dtype=np.float64)
            )
            combined_count = count + chunk_count
            delta = chunk_mean - mean
            mean += delta * chunk_count / combined_count
            m2 += chunk_m2 + delta * delta * count * chunk_count / combined_count
            count = combined_count
            minimum = min(minimum, float(np.min(values, where=valid, initial=np.inf)))
            maximum = max(maximum, float(np.max(values, where=valid, initial=-np.inf)))

        if count == 0:
            raise FitsStatisticsError()

        median = _sampled_median(hdu, count)
        variance = max(0.0, m2 / count)
        return BasicStatistics(
            minimum=minimum,
            maximum=maximum,
            mean=mean,
            median=median,
            standard_deviation=float(np.sqrt(variance)),
            finite_pixel_count=count,
        )
    except FitsStatisticsError:
        raise
    except (ArithmeticError, MemoryError, TypeError, ValueError) as exc:
        raise FitsStatisticsError() from exc


def _sampled_median(hdu: Any, finite_count: int) -> float:
    sample_count = min(finite_count, MEDIAN_SAMPLE_LIMIT)
    ranks = np.linspace(0, finite_count - 1, num=sample_count, dtype=np.int64)
    sample = np.empty(sample_count, dtype=np.float64)
    finite_offset = 0
    sample_offset = 0

    for values, valid in _physical_chunks(hdu):
        valid_positions = np.flatnonzero(valid.reshape(-1))
        next_finite_offset = finite_offset + int(valid_positions.size)
        next_sample_offset = int(np.searchsorted(ranks, next_finite_offset, side="left"))
        if next_sample_offset > sample_offset:
            local_ranks = ranks[sample_offset:next_sample_offset] - finite_offset
            selected_positions = valid_positions[local_ranks]
            sample[sample_offset:next_sample_offset] = values.reshape(-1)[selected_positions]
            sample_offset = next_sample_offset
        finite_offset = next_finite_offset

    if sample_offset != sample_count:
        raise FitsStatisticsError()
    return float(np.median(sample))


def _safe_header(header: fits.Header) -> dict[str, str | int | float | bool]:
    result: dict[str, str | int | float | bool] = {}
    for card in header.cards[:HEADER_SCAN_LIMIT]:
        key = str(card.keyword).strip()
        if not key or key.upper() in {"COMMENT", "HISTORY"}:
            continue
        previous = result.get(key)
        result[key] = _safe_header_value(card.value)
        if _serialized_header_size(result) > HEADER_TOTAL_BYTES_LIMIT:
            if previous is None:
                result.pop(key)
            else:
                result[key] = previous
            break
        if len(result) >= HEADER_ENTRY_LIMIT:
            break
    return result


def _serialized_header_size(header: dict[str, str | int | float | bool]) -> int:
    return len(
        json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _safe_header_value(value: Any) -> str | int | float | bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        converted = float(value)
        return converted if np.isfinite(converted) else str(converted)
    if isinstance(value, str):
        return value[:HEADER_VALUE_LENGTH_LIMIT]
    return str(value)[:HEADER_VALUE_LENGTH_LIMIT]
