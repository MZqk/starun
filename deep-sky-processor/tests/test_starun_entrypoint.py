import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "api"))

from app.agent_sdk.contracts import ProcessingSkillResult


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


starun_processing = load_module(
    "processor_starun_runner_e2e",
    ROOT / "scripts" / "run_starun_processing.py",
)


class StarunProcessingEntrypointTests(unittest.TestCase):
    def test_balanced_entrypoint_writes_sdk_result_and_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            source = input_dir / "source.fits"
            source.write_bytes(b"synthetic")
            request = {
                "schema_version": "starun.skill-request/v1",
                "task_id": "task-1",
                "task_type": "processing",
                "source_path": "input/source.fits",
                "inspection_path": "input/inspection.json",
                "output_dir": "output",
                "style": "balanced",
            }
            inspection = {"format": "fits", "shape": [32, 32]}
            request_path = input_dir / "request.json"
            inspection_path = input_dir / "inspection.json"
            result_path = output_dir / "processing-result.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            inspection_path.write_text(json.dumps(inspection), encoding="utf-8")

            def fake_preview(_source_path, preview_dir):
                preview_dir = Path(preview_dir)
                preview_dir.mkdir(parents=True, exist_ok=True)
                preview = preview_dir / "safe_full.png"
                Image.new("RGB", (16, 12), color=(24, 28, 32)).save(preview)
                return {"paths": {"full": str(preview)}}

            def fake_pipeline(**kwargs):
                output_path = Path(kwargs["output_path"])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (20, 14), color=(32, 36, 40)).save(output_path)
                payload = {
                    "schema_version": "1.0",
                    "status": "success",
                    "outputs": {"image": str(output_path)},
                    "effective_config": {
                        "preset": "adaptive",
                        "steps": ["color", "stretch", "style"],
                        "target_type": "emission_nebula",
                        "target_name": "NGC6888",
                        "style": "dramatic_nebula",
                        "style_strength": 1.0,
                    },
                    "quality_gates": [],
                    "warnings": [],
                }
                Path(kwargs["result_json"]).write_text(
                    json.dumps(payload),
                    encoding="utf-8",
                )
                return payload

            with (
                patch.object(starun_processing.recognize, "create_safe_preview_bundle", side_effect=fake_preview),
                patch.object(starun_processing.pipeline, "run_pipeline", side_effect=fake_pipeline),
            ):
                starun_processing.run(
                    source_path=source,
                    output_dir=output_dir,
                    result_path=result_path,
                    request_path=request_path,
                    inspection_path=inspection_path,
                )

            result = ProcessingSkillResult.model_validate_json(
                result_path.read_bytes(),
                strict=True,
            )
            self.assertEqual(result.style.value, "balanced")
            self.assertEqual(result.reference_artifact, "reference.jpg")
            self.assertEqual(result.result_artifact, "result.jpg")
            self.assertEqual(result.result_width, 20)
            self.assertEqual(result.result_height, 14)
            artifact_names = {artifact.name for artifact in result.artifacts}
            self.assertEqual(
                artifact_names,
                {"reference.jpg", "style-prompt.json", "result.jpg", "pipeline-result.json"},
            )


if __name__ == "__main__":
    unittest.main()
