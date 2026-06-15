from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits  # type: ignore[import-untyped]
from PIL import Image


MAX_PREVIEW_EDGE = 1600


@dataclass(frozen=True)
class FitsPreview:
    data: bytes
    width: int
    height: int
    lower_percentile: float
    upper_percentile: float


def render_fits_preview(path: Path, hdu_index: int) -> FitsPreview:
    with fits.open(
        path,
        memmap=True,
        lazy_load_hdus=True,
        do_not_scale_image_data=True,
    ) as hdus:
        hdu = hdus[hdu_index]
        if hdu.data is None:
            raise ValueError("selected FITS HDU has no image data")
        image = _sample_image(hdu.data)
        image = _physical_values(image, hdu.header)

    stretched, lower, upper = _stretch(image)
    encoded = _encode_png(stretched)
    return FitsPreview(
        data=encoded,
        width=int(stretched.shape[1]),
        height=int(stretched.shape[0]),
        lower_percentile=lower,
        upper_percentile=upper,
    )


def _sample_image(data: Any) -> np.ndarray[Any, np.dtype[np.float32]]:
    shape = tuple(int(length) for length in data.shape)
    if len(shape) == 2:
        step = max(1, int(np.ceil(max(shape) / MAX_PREVIEW_EDGE)))
        return np.array(data[::step, ::step], dtype=np.float32, copy=True)
    if len(shape) != 3:
        raise ValueError("selected FITS HDU is not a supported image")

    channel_axis = next((axis for axis, length in enumerate(shape) if length == 3), None)
    if channel_axis is None:
        raise ValueError("selected FITS cube has no RGB channel axis")
    spatial_shape = tuple(length for axis, length in enumerate(shape) if axis != channel_axis)
    step = max(1, int(np.ceil(max(spatial_shape) / MAX_PREVIEW_EDGE)))
    if channel_axis == 0:
        sampled = data[:, ::step, ::step]
        sampled = np.moveaxis(sampled, 0, -1)
    elif channel_axis == 1:
        sampled = data[::step, :, ::step]
        sampled = np.moveaxis(sampled, 1, -1)
    else:
        sampled = data[::step, ::step, :]
    return np.array(sampled, dtype=np.float32, copy=True)


def _physical_values(
    image: np.ndarray[Any, np.dtype[np.float32]],
    header: fits.Header,
) -> np.ndarray[Any, np.dtype[np.float32]]:
    blank = header.get("BLANK")
    if isinstance(blank, (int, float)):
        image[image == float(blank)] = np.nan
    bscale = float(header.get("BSCALE", 1.0))
    bzero = float(header.get("BZERO", 0.0))
    if bscale != 1.0:
        image *= bscale
    if bzero != 0.0:
        image += bzero
    return image


def _stretch(
    image: np.ndarray[Any, np.dtype[np.float32]],
) -> tuple[np.ndarray[Any, np.dtype[np.uint8]], float, float]:
    finite = image[np.isfinite(image)]
    if finite.size == 0:
        raise ValueError("selected FITS HDU has no finite pixels")
    lower, upper = (float(value) for value in np.percentile(finite, [0.5, 99.8]))
    if upper <= lower:
        upper = lower + 1.0
    normalized = np.nan_to_num(
        (image - lower) / (upper - lower),
        nan=0.0,
        posinf=1.0,
        neginf=0.0,
    )
    np.clip(normalized, 0.0, 1.0, out=normalized)
    normalized = np.arcsinh(normalized * 10.0) / np.arcsinh(10.0)
    if normalized.ndim == 2:
        normalized = np.repeat(normalized[:, :, np.newaxis], 3, axis=2)
    return np.rint(normalized * 255.0).astype(np.uint8), lower, upper


def _encode_png(image: np.ndarray[Any, np.dtype[np.uint8]]) -> bytes:
    output = BytesIO()
    Image.fromarray(image, mode="RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()
