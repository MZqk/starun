import subprocess
import warnings
from pathlib import Path

from astropy.io import fits
from astropy.io.fits.verify import VerifyWarning

from app.fits.inspector import inspect_fits


def test_e2e_generator_produces_strict_fits_with_extension_one_selected(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    generator = repository_root / "web" / "e2e" / "fixtures" / "fits.ts"
    fixture = tmp_path / "starun-e2e.fits"
    script = (
        f"import({generator.as_uri()!r}).then("
        "module => process.stdout.write(module.deterministicFitsFixture()))"
    )
    with fixture.open("wb") as output:
        subprocess.run(
            ["node", "--experimental-strip-types", "-e", script],
            check=True,
            cwd=repository_root,
            stdout=output,
        )

    with warnings.catch_warnings():
        warnings.simplefilter("error", VerifyWarning)
        with fits.open(fixture) as hdus:
            hdus.verify("exception")

    inspection = inspect_fits(fixture)
    assert inspection.selected_hdu.index == 1
    assert inspection.selected_hdu.name == "LARGE_IMAGE"
