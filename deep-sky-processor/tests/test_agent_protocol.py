import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from skimage.io import imsave

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_protocol import (
    create_review_bundle,
    evaluate_quality_gates,
    intent_to_overrides,
    validate_action,
    validate_review,
)
from agent_workflow import apply_action, initialize_session
from pipeline import run_pipeline
from pipeline import (
    _dbe_candidate_plan,
    apply_low_snr_linear_guards,
    apply_marginal_starless_guards,
    protect_style_background_floor,
    recover_crushed_background,
    safe_remove_gradient,
)


def write_scene(path, background=0.04):
    image = np.full((48, 64, 3), background, dtype=np.float32)
    yy, xx = np.mgrid[:48, :64]
    nebula = np.exp(-(((xx - 32) / 14) ** 2 + ((yy - 24) / 10) ** 2))
    image[..., 0] += nebula * 0.35
    image[..., 1] += nebula * 0.10
    image[..., 2] += nebula * 0.08
    imsave(path, np.clip(image * 255, 0, 255).astype(np.uint8))


class AgentProtocolTests(unittest.TestCase):
    def test_semantic_intent_maps_to_bounded_params(self):
        params = intent_to_overrides({
            "background": "slightly_darker",
            "core_protection": "strong",
            "star_dominance": "reduce_slightly",
            "noise_tolerance": "preserve_detail",
        })
        self.assertEqual(params["target_bg"], 0.07)
        self.assertEqual(params["ghs_protect_strength"], 0.75)
        self.assertLessEqual(params["star_reduction"], 0.18)
        self.assertLessEqual(params["final_denoise_lum"], 0.006)

    def test_action_and_review_validation(self):
        action = validate_action({
            "operation": "run_step",
            "step": "stretch",
            "intent": {"background": "slightly_darker"},
        })
        self.assertEqual(action["step"], "stretch")
        review = validate_review({
            "verdict": "retry",
            "actions": [action],
        })
        self.assertEqual(review["verdict"], "retry")
        with self.assertRaises(ValueError):
            validate_action({"operation": "invent_pixels"})

    def test_quality_gate_marks_dbe_corner_failure_for_review(self):
        status, gates = evaluate_quality_gates(
            {
                "median": 0.08,
                "corner_uniformity_ratio": 4.2,
                "uniform_5x5_dark_patch_ratio": 0.1,
                "star_area_ratio": 0.02,
            },
            target_type="emission_nebula",
            steps=["dbe"],
        )
        self.assertEqual(status, "review_required")
        self.assertEqual(gates[0]["code"], "CORNER_NONUNIFORM")
        self.assertEqual(gates[0]["status"], "failed")

    def test_linear_negative_background_does_not_use_final_median_gate(self):
        status, gates = evaluate_quality_gates(
            {
                "processing_stage": "linear",
                "median": -0.003,
                "p1": -0.01,
                "negative_pixel_ratio": 0.2,
                "nonpositive_pixel_ratio": 0.2,
                "corner_uniformity_ratio": 1.0,
                "uniform_5x5_dark_patch_ratio": 0.0,
                "star_area_ratio": 0.0,
            },
            target_type="emission_nebula",
        )
        codes = {gate["code"] for gate in gates}
        self.assertEqual(status, "review_required")
        self.assertIn("LINEAR_BACKGROUND_UNDERSHOOT", codes)
        self.assertNotIn("BACKGROUND_CRUSHED", codes)
        self.assertNotIn("BACKGROUND_LOW", codes)

    def test_final_background_crush_uses_clipping_evidence(self):
        _status, gates = evaluate_quality_gates(
            {
                "processing_stage": "final",
                "median": 0.08,
                "p1": 0.0,
                "negative_pixel_ratio": 0.0,
                "nonpositive_pixel_ratio": 0.04,
                "corner_uniformity_ratio": 1.0,
                "uniform_5x5_dark_patch_ratio": 0.0,
                "star_area_ratio": 0.0,
            },
            target_type="emission_nebula",
        )
        self.assertIn("BACKGROUND_CRUSHED", {gate["code"] for gate in gates})

    def test_review_bundle_contains_visual_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            before = Path(td) / "before.png"
            after = Path(td) / "after.png"
            review_dir = Path(td) / "review"
            write_scene(before, 0.04)
            write_scene(after, 0.06)

            payload = create_review_bundle(
                before,
                after,
                review_dir,
                context={"target_type": "emission_nebula", "steps": ["stretch"]},
            )

            self.assertTrue(Path(payload["report_path"]).exists())
            for path in payload["previews"].values():
                self.assertTrue(Path(path).exists())
            self.assertIn("critic_checklist", payload)
            self.assertIn("metrics_before", payload)
            self.assertIn("metric_delta", payload)

    def test_pipeline_returns_machine_result(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            output = Path(td) / "output.tif"
            result_path = Path(td) / "result.json"
            write_scene(source)

            result = run_pipeline(
                str(source),
                str(output),
                steps="stretch",
                preset="light",
                cleanup=True,
                result_json=str(result_path),
            )

            self.assertIn(result["status"], {"success", "review_required"})
            self.assertEqual(result["schema_version"], "1.0")
            self.assertEqual(result["outputs"]["image"], str(output))
            self.assertIn("quality_gates", result)
            persisted = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema_version"], "1.0")

    def test_low_snr_linear_guards_bound_emission_stretch(self):
        cfg, log = apply_low_snr_linear_guards(
            {
                "stretch_factor": 153.0,
                "target_bg": 0.06,
                "final_denoise_lum": 0.018,
            },
            is_linear_input=True,
            effective_target_type="emission_nebula",
            physical_priors={
                "recommendations": [
                    "warm_sensor_stronger_noise_control",
                    "short_subexposure_expect_read_noise",
                ],
            },
        )

        self.assertEqual(cfg["stretch_factor"], 110.0)
        self.assertGreaterEqual(cfg["target_bg"], 0.085)
        self.assertLessEqual(cfg["final_denoise_lum"], 0.010)
        self.assertTrue(log)

    def test_marginal_starnet_guards_reduce_downstream_aggression(self):
        cfg, log = apply_marginal_starless_guards(
            {
                "hdr_strength": 0.45,
                "final_denoise_lum": 0.018,
                "star_combine_strength": 1.0,
            },
            {
                "repair_quality_score": 0.45,
                "nebula_damage_ratio": 0.0883,
                "fallback_applied": False,
            },
        )

        self.assertLessEqual(cfg["hdr_strength"], 0.30)
        self.assertLessEqual(cfg["final_denoise_lum"], 0.008)
        self.assertLessEqual(cfg["star_combine_strength"], 0.82)
        self.assertTrue(log)

    def test_crushed_background_recovery_lifts_p1(self):
        image = np.zeros((32, 32, 3), dtype=np.float32)
        image[8:24, 8:24, :] = 0.03

        recovered, report = recover_crushed_background(
            image,
            target_type="emission_nebula",
        )
        p1 = float(np.percentile(np.mean(recovered[..., :3], axis=2), 1.0))

        self.assertTrue(report["applied"])
        self.assertGreater(p1, 0.0001)

    def test_style_background_floor_lifts_low_emission_median(self):
        image = np.full((32, 32, 3), 0.025, dtype=np.float32)
        image[10:22, 10:22, 0] = 0.18

        protected, report = protect_style_background_floor(
            image,
            target_type="emission_nebula",
        )
        median = float(np.median(np.mean(protected[..., :3], axis=2)))

        self.assertTrue(report["applied"])
        self.assertGreaterEqual(median, 0.04)
        self.assertLessEqual(float(np.max(protected)), 1.0)

    def test_safe_dbe_skips_when_all_candidates_crush_background(self):
        image = np.full((32, 32, 3), 0.04, dtype=np.float32)

        def crushing_remove_gradient(source, **_kwargs):
            return np.zeros_like(source), np.ones_like(source) * 0.2

        with patch("pipeline.remove_gradient", side_effect=crushing_remove_gradient):
            corrected, report = safe_remove_gradient(
                image,
                {"dbe_method": "rbf", "dbe_degree": 3},
                target_type="emission_nebula",
            )

        self.assertEqual(report["status"], "skipped_unsafe")
        self.assertIsNone(report["selected"])
        self.assertTrue(np.allclose(corrected, image))

    def test_emission_dbe_plan_does_not_add_rbf_by_default(self):
        plan = _dbe_candidate_plan(
            {"dbe_method": "polynomial", "dbe_degree": 1},
            target_type="emission_nebula",
        )

        self.assertEqual({item["method"] for item in plan}, {"polynomial"})
        self.assertLessEqual(max(item["strength"] for item in plan), 0.25)

    def test_emission_dbe_plan_requires_explicit_rbf_opt_in(self):
        plan = _dbe_candidate_plan(
            {"dbe_method": "rbf", "_dbe_allow_rbf": True},
            target_type="emission_nebula",
        )

        self.assertIn("rbf", {item["method"] for item in plan})

    def test_session_executes_one_step_and_waits_for_review(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            session_dir = Path(td) / "session"
            write_scene(source)
            state = initialize_session(
                str(source),
                session_dir,
                target_type="emission_nebula",
            )
            self.assertEqual(state["current_artifact"], str(source.resolve()))

            event = apply_action(session_dir, {
                "operation": "run_step",
                "step": "stretch",
                "preset": "light",
                "intent": {"core_protection": "strong"},
            })

            self.assertEqual(event["operation"], "run_step")
            updated = json.loads(
                (session_dir / "session.json").read_text(encoding="utf-8")
            )
            self.assertTrue(Path(updated["current_artifact"]).exists())
            self.assertTrue(Path(updated["pending_review"]).exists())

    def test_session_accepts_normalized_local_region(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            session_dir = Path(td) / "session"
            write_scene(source)
            initialize_session(str(source), session_dir)

            event = apply_action(session_dir, {
                "operation": "run_step",
                "step": "local_enhance",
                "preset": "light",
                "region": {"bbox": [0.25, 0.25, 0.5, 0.5]},
                "local_strength": 0.15,
            })

            result = event["result"]
            self.assertTrue(Path(result["artifact"]).exists())
            self.assertEqual(
                result["review"]["context"]["region"]["bbox"],
                [0.25, 0.25, 0.5, 0.5],
            )


if __name__ == "__main__":
    unittest.main()
