from collections.abc import Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt
from astropy.io import fits


Array = npt.NDArray[np.generic]


def make_fits(
    tmp_path: Path,
    primary_data: Array | None,
    extensions: Sequence[Array] = (),
    *,
    primary_header: fits.Header | None = None,
    name: str = "image.fits",
) -> Path:
    path = tmp_path / name
    hdus: list[fits.hdu.base.ExtensionHDU | fits.PrimaryHDU] = [
        fits.PrimaryHDU(data=primary_data, header=primary_header)
    ]
    hdus.extend(
        fits.ImageHDU(data=data, name=f"IMAGE_{index}")
        for index, data in enumerate(extensions, start=1)
    )
    fits.HDUList(hdus).writeto(path)
    return path


def make_table_only_fits(tmp_path: Path) -> Path:
    path = tmp_path / "table-only.fits"
    column = fits.Column(name="flux", format="E", array=np.array([1.0, 2.0], dtype=np.float32))
    fits.HDUList([fits.PrimaryHDU(), fits.BinTableHDU.from_columns([column])]).writeto(path)
    return path


def make_corrupt_fits(tmp_path: Path) -> Path:
    path = tmp_path / "corrupt.fits"
    path.write_bytes(b"this is not a FITS file")
    return path


def make_rgb_fits(tmp_path: Path, shape: tuple[int, int, int], *, name: str) -> Path:
    data = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    return make_fits(tmp_path, data, name=name)


def make_uint16_fits(tmp_path: Path) -> tuple[Path, Array]:
    data = np.array([[0, 1], [32768, 65535]], dtype=np.uint16)
    return make_fits(tmp_path, data, name="uint16.fits"), data
