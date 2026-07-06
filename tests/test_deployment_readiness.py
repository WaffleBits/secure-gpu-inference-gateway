import copy
import json
import tempfile
import unittest
from pathlib import Path

from gateway.capacity_plan import build_capacity_report
from gateway.deployment_readiness import (
    DeploymentPhase,
    DeploymentThresholds,
    build_deployment_readiness_report,
    write_deployment_readiness_report,
)
from gateway.registry import MODEL_POLICIES


class DeploymentReadinessTest(unittest.TestCase):
    def test_builds_public_safe_deployment_readiness_report(self) -> None:
        report = build_deployment_readiness_report()

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["readiness_status"], "pass")
        self.assertEqual(
            report["rollout_recommendation"],
            "ready_for_local_deployment_review",
        )
        self.assertEqual(
            {model["model_id"] for model in report["phases"][-1]["models"]},
            set(MODEL_POLICIES),
        )
        gate_by_name = {gate["name"]: gate for gate in report["release_gates"]}
        self.assertEqual(gate_by_name["capacity_plan_passed"]["status"], "pass")
        self.assertEqual(gate_by_name["workload_readiness_passed"]["status"], "pass")
        self.assertEqual(
            gate_by_name["distributed_limiter_evidence_passed"]["status"],
            "pass",
        )
        self.assertLessEqual(
            gate_by_name["staged_capacity_utilization"]["observed_max"],
            gate_by_name["staged_capacity_utilization"]["threshold"],
        )

    def test_capacity_hold_blocks_deployment_readiness(self) -> None:
        capacity_report = copy.deepcopy(build_capacity_report())
        capacity_report["models"][0]["status"] = "exceeds_synthetic_capacity"

        report = build_deployment_readiness_report(capacity_report=capacity_report)

        self.assertEqual(report["readiness_status"], "hold")
        self.assertEqual(report["rollout_recommendation"], "hold_for_release_review")
        gate_by_name = {gate["name"]: gate for gate in report["release_gates"]}
        self.assertEqual(gate_by_name["capacity_plan_passed"]["status"], "hold")

    def test_utilization_threshold_can_hold_a_rollout(self) -> None:
        report = build_deployment_readiness_report(
            thresholds=DeploymentThresholds(max_phase_policy_utilization=0.01),
        )

        self.assertEqual(report["readiness_status"], "hold")
        gate_by_name = {gate["name"]: gate for gate in report["release_gates"]}
        self.assertEqual(gate_by_name["staged_capacity_utilization"]["status"], "hold")

    def test_invalid_phase_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "rollback_trigger"):
            build_deployment_readiness_report(
                phases=(
                    DeploymentPhase(
                        name="canary",
                        traffic_fraction=0.1,
                        capacity_reservation_fraction=0.1,
                        validation_mode="canary",
                        rollback_trigger="",
                        required_evidence=("artifacts/capacity-plan-evidence.json",),
                    ),
                ),
            )

    def test_written_artifact_excludes_sensitive_payload_and_identity_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "deployment-readiness-evidence.json"

            write_deployment_readiness_report(output_path)

            artifact = output_path.read_text(encoding="utf-8")
            parsed = json.loads(artifact)
            self.assertEqual(parsed["readiness_status"], "pass")

            forbidden_fragments = (
                "access_reason",
                "auth_subject",
                "credential",
                "decoded_text",
                "generated_text",
                "model_output",
                "principal_id",
                "prompt",
                "raw_input",
                "request_body",
                "secret_value",
                "subject_id",
            )
            lowered = artifact.lower()
            for fragment in forbidden_fragments:
                self.assertNotIn(fragment, lowered)


if __name__ == "__main__":
    unittest.main()
