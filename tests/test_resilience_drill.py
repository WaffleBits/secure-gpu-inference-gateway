import json
import tempfile
import unittest
from pathlib import Path

from gateway.resilience_drill import (
    REQUIRED_SCENARIOS,
    ResilienceProbe,
    ResilienceThresholds,
    build_resilience_drill_report,
    write_resilience_drill_report,
)


class ResilienceDrillTest(unittest.TestCase):
    def test_builds_public_safe_resilience_drill_report(self) -> None:
        report = build_resilience_drill_report()

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["readiness_status"], "pass")
        self.assertEqual(report["rollout_recommendation"], "ready_for_resilience_review")
        self.assertEqual(
            report["scenario_coverage"]["observed_scenarios"],
            sorted(REQUIRED_SCENARIOS),
        )
        gate_by_name = {gate["name"]: gate for gate in report["release_gates"]}
        self.assertEqual(gate_by_name["workload_readiness_passed"]["status"], "pass")
        self.assertEqual(gate_by_name["deployment_readiness_passed"]["status"], "pass")
        self.assertEqual(gate_by_name["resilience_scenario_coverage"]["status"], "pass")
        self.assertEqual(gate_by_name["resilience_probe_thresholds"]["status"], "pass")

    def test_probe_threshold_violation_holds_drill(self) -> None:
        report = build_resilience_drill_report(
            probes=(
                ResilienceProbe(
                    name="error_rate_regression",
                    scenario="backend_error_burst",
                    affected_model="threat-triage",
                    injected_condition="synthetic error burst",
                    detection_signal="error-rate gate trips",
                    mitigation_path="hold the canary",
                    rollback_action="restore prior backend target",
                    baseline_p95_latency_ms=500.0,
                    observed_p95_latency_ms=520.0,
                    observed_error_rate=0.25,
                    observed_queue_depth=4,
                    recovery_seconds=10.0,
                ),
            ),
            thresholds=ResilienceThresholds(required_scenarios=("backend_error_burst",)),
        )

        self.assertEqual(report["readiness_status"], "hold")
        gate_by_name = {gate["name"]: gate for gate in report["release_gates"]}
        self.assertEqual(gate_by_name["resilience_probe_thresholds"]["status"], "hold")
        self.assertEqual(
            gate_by_name["resilience_probe_thresholds"]["held_probes"],
            ["error_rate_regression"],
        )

    def test_missing_required_scenario_holds_drill(self) -> None:
        report = build_resilience_drill_report(
            probes=(
                ResilienceProbe(
                    name="latency_only",
                    scenario="backend_latency_spike",
                    affected_model="mission-summarizer",
                    injected_condition="synthetic latency spike",
                    detection_signal="latency gate trips",
                    mitigation_path="hold route change",
                    rollback_action="restore prior route",
                    baseline_p95_latency_ms=400.0,
                    observed_p95_latency_ms=500.0,
                    observed_error_rate=0.0,
                    observed_queue_depth=6,
                    recovery_seconds=11.0,
                ),
            ),
            thresholds=ResilienceThresholds(
                required_scenarios=("backend_latency_spike", "queue_saturation"),
            ),
        )

        self.assertEqual(report["readiness_status"], "hold")
        self.assertEqual(
            report["scenario_coverage"]["missing_scenarios"],
            ["queue_saturation"],
        )

    def test_invalid_probe_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "baseline_p95_latency_ms"):
            build_resilience_drill_report(
                probes=(
                    ResilienceProbe(
                        name="invalid",
                        scenario="backend_latency_spike",
                        affected_model="mission-summarizer",
                        injected_condition="synthetic latency spike",
                        detection_signal="latency gate trips",
                        mitigation_path="hold route change",
                        rollback_action="restore prior route",
                        baseline_p95_latency_ms=0.0,
                        observed_p95_latency_ms=500.0,
                        observed_error_rate=0.0,
                        observed_queue_depth=6,
                        recovery_seconds=11.0,
                    ),
                ),
            )

    def test_written_artifact_excludes_sensitive_payload_and_identity_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "resilience-drill-evidence.json"

            write_resilience_drill_report(output_path)

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
