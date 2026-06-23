from pathlib import Path

import numpy as np
from PIL import Image

from app.analysis.preview import render_image_preview
from tests.fixtures.fits_factory import make_xisf


def test_renders_xisf_preview(tmp_path: Path) -> None:
    data = np.linspace(0, 1, 60, dtype=np.float32).reshape(6, 10)
    path = make_xisf(tmp_path, data)

    preview = render_image_preview(path, 0, max_edge=8)

    assert preview.width <= 8
    assert preview.height <= 8
    output = tmp_path / "preview.png"
    output.write_bytes(preview.data)
    with Image.open(output) as image:
        assert image.mode == "RGB"
        assert image.size == (preview.width, preview.height)
