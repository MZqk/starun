import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT.parent / "api"))

from app.agent_sdk.contracts import AnalysisSkillResult


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyzer = load_module("advisor_analyzer_e2e", ROOT / "scripts" / "analyze_file.py")
advisor = load_module("advisor_generator_e2e", ROOT / "scripts" / "generate_advice.py")
starun_runner = load_module("advisor_starun_runner_e2e", ROOT / "scripts" / "run_starun_analysis.py")


class EndToEndTests(unittest.TestCase):
    def test_starun_entrypoint_resolves_parent_relative_output_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            skill_dir = root / ".agents" / "deep-sky-advisor"
            output_dir = root / "output"
            input_dir.mkdir()
            skill_dir.mkdir(parents=True)
            output_dir.mkdir()
            (input_dir / "request.json").write_text("{}", encoding="utf-8")

            previous_cwd = Path.cwd()
            try:
                os.chdir(skill_dir)
                resolved = starun_runner._sandbox_path("../../output/analysis-result.json")
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(
                resolved.resolve(),
                (output_dir / "analysis-result.json").resolve(),
            )

    def test_analysis_to_audited_advice(self):
        height, width = 160, 220
        yy, xx = np.mgrid[0:height, 0:width]
        rng = np.random.default_rng(12)
        mono = 0.04 + 0.09 * xx / width + rng.normal(0, 0.004, (height, width))
        for y, x in ((30, 30), (40, 100), (65, 180), (90, 60), (115, 140), (135, 200)):
            mono += 0.7 * np.exp(-0.5 * (((xx - x) / 1.7) ** 2 + ((yy - y) / 1.5) ** 2))
        rgb = np.stack([mono * 1.2, mono, mono * 0.75], axis=0).astype(np.float32)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "ngc6888_integrated.fits"
            hdu = fits.PrimaryHDU(rgb)
            hdu.header["OBJECT"] = "NGC6888"
            hdu.header["FILTER"] = "DualBand"
            hdu.header["NCOMBINE"] = 36
            hdu.writeto(input_path)

            analysis = analyzer.analyze_image_file(input_path, root / "out")
            advice = advisor.compile_advice(
                analysis,
                software="pixinsight",
                target_type="emission_nebula",
                target_name="NGC6888",
                filter_name="DualBand",
            )

            self.assertEqual(advisor.validate_advice(advice), [])
            operations = {op["id"]: op for op in advice["operations"]}
            self.assertEqual(operations["background_review"]["decision"], "review")
            self.assertIn("narrowband_mapping", operations)
            self.assertNotIn("color_calibration", operations)
            self.assertEqual(advice["source_analysis_schema"], "2.0")

    def test_starun_entrypoint_writes_sdk_result_and_artifacts(self):
        height, width = 120, 160
        yy, xx = np.mgrid[0:height, 0:width]
        mono = 0.05 + 0.05 * xx / width
        for y, x in ((35, 40), (70, 90), (95, 135)):
            mono += 0.6 * np.exp(-0.5 * (((xx - x) / 1.8) ** 2 + ((yy - y) / 1.7) ** 2))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            input_path = input_dir / "source.fits"
            hdu = fits.PrimaryHDU(mono.astype(np.float32))
            hdu.header["OBJECT"] = "M31"
            hdu.header["NCOMBINE"] = 24
            hdu.writeto(input_path)
            (input_dir / "request.json").write_text(
                '{"source_path":"input/source.fits"}',
                encoding="utf-8",
            )

            result_path = output_dir / "analysis-result.json"
            code = starun_runner.main([
                "--source", str(input_path),
                "--output-dir", str(output_dir),
                "--result", str(result_path),
                "--request-json", str(input_dir / "request.json"),
            ])

            self.assertEqual(code, 0)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(result["schema_version"], "starun.skill-result/v1")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["preview"]["artifact"], "analysis-preview.png")
            self.assertEqual(
                result["artifacts"],
                [
                    {"name": "analysis-report.json", "media_type": "application/json"},
                    {"name": "analysis-preview.png", "media_type": "image/png"},
                ],
            )
            self.assertTrue((output_dir / "analysis-report.json").exists())
            self.assertTrue((output_dir / "analysis-preview.png").exists())
            self.assertTrue((output_dir / "analysis-processing-report.md").exists())
            self.assertGreaterEqual(len(result["analysis"]["workflow"]), 4)
            self.assertNotIn("preview_metadata", result["analysis"])
            AnalysisSkillResult.model_validate_json(
                result_path.read_text(encoding="utf-8"),
                strict=True,
            )


if __name__ == "__main__":
    unittest.main()
