import json
import tempfile
import unittest
from pathlib import Path

from gateway.capacity_plan import (
    CapacityAssumption,
    CapacityProfile,
    build_capacity_report,
    cost_per_units,
    write_capacity_report,
)
from gateway.registry import MODEL_POLICIES


class CapacityPlanTest(unittest.TestCase):
    def test_builds_capacity_and_cost_estimates_for_all_policy_models(self) -> None:
        report = build_capacity_report()

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(
            {model["model_id"] for model in report["models"]},
            set(MODEL_POLICIES),
        )
        for model in report["models"]:
            self.assertGreater(model["modeled_requests_per_minute"], 0)
            self.assertGreater(model["modeled_input_tokens_per_minute"], 0)
            self.assertGreater(model["cost_per_1000_requests_usd"], 0)
            self.assertGreater(model["cost_per_1m_input_tokens_usd"], 0)
            self.assertIn(
                model["status"],
                {"within_synthetic_capacity", "exceeds_synthetic_capacity"},
            )

    def test_capacity_status_flags_policy_that_exceeds_synthetic_profile(self) -> None:
        profile = CapacityProfile(
            model_id="mission-summarizer",
            profile_name="tiny-profile",
            measured_requests_per_second=0.1,
            measured_input_tokens_per_second=10,
            measured_decode_tokens_per_second=5,
            observed_p95_latency_ms=500,
            observed_gpu_utilization=0.9,
            gpu_hourly_cost_usd=2.0,
        )

        report = build_capacity_report(
            profiles=(profile,),
            assumption=CapacityAssumption(target_gpu_utilization=0.5, safety_margin=0.5),
        )

        self.assertEqual(
            report["models"][0]["status"],
            "exceeds_synthetic_capacity",
        )

    def test_invalid_profiles_are_rejected(self) -> None:
        profile = CapacityProfile(
            model_id="mission-summarizer",
            profile_name="invalid",
            measured_requests_per_second=1,
            measured_input_tokens_per_second=1,
            measured_decode_tokens_per_second=1,
            observed_p95_latency_ms=1,
            observed_gpu_utilization=0,
            gpu_hourly_cost_usd=1,
        )

        with self.assertRaisesRegex(ValueError, "observed_gpu_utilization"):
            build_capacity_report(profiles=(profile,))

    def test_cost_per_units_validates_positive_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "hourly_cost"):
            cost_per_units(0, 10, 1000)
        with self.assertRaisesRegex(ValueError, "units_per_second"):
            cost_per_units(1, 0, 1000)

    def test_written_artifact_is_public_safe_aggregate_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "capacity-plan-evidence.json"

            write_capacity_report(output_path)

            artifact = output_path.read_text(encoding="utf-8")
            parsed = json.loads(artifact)
            self.assertEqual(parsed["schema_version"], 1)
            forbidden_fragments = (
                "access_reason",
                "auth_subject",
                "credential",
                "generated_text",
                "model_output",
                "principal_id",
                "prompt",
                "raw_input",
                "subject",
            )
            lowered = artifact.lower()
            for fragment in forbidden_fragments:
                self.assertNotIn(fragment, lowered)


if __name__ == "__main__":
    unittest.main()
