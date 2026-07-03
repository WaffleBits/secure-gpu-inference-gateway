import json
import tempfile
import unittest
from pathlib import Path

from gateway.workload_replay import (
    ReplayRequest,
    ReplayThresholds,
    build_workload_report,
    write_workload_report,
)


class WorkloadReplayTest(unittest.TestCase):
    def test_builds_public_safe_readiness_report_with_guardrail_coverage(self) -> None:
        report = build_workload_report()

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["readiness_status"], "pass")
        self.assertEqual(report["rollout_recommendation"], "ready_for_local_review")
        self.assertEqual(
            set(report["outcomes"]),
            {"allowed", "policy_denied", "rate_limited", "token_budget_limited"},
        )
        self.assertGreater(report["outcomes"]["allowed"], 0)
        self.assertGreater(report["latency_ms"]["p95"], 0)
        self.assertLessEqual(
            report["latency_ms"]["p95"],
            report["thresholds"]["max_allowed_p95_latency_ms"],
        )
        self.assertEqual(report["guardrail_coverage"]["missing_outcomes"], [])

    def test_missing_required_outcome_holds_the_replay(self) -> None:
        report = build_workload_report(
            requests=(ReplayRequest("analyst-1", "benchmark-echo", 16),),
            thresholds=ReplayThresholds(required_outcomes=("allowed", "policy_denied")),
        )

        self.assertEqual(report["readiness_status"], "hold")
        self.assertEqual(report["rollout_recommendation"], "hold_for_investigation")
        self.assertEqual(report["guardrail_coverage"]["missing_outcomes"], ["policy_denied"])
        gate_by_name = {gate["name"]: gate for gate in report["release_gates"]}
        self.assertEqual(gate_by_name["guardrail_outcome_coverage"]["status"], "hold")

    def test_written_artifact_excludes_sensitive_request_and_identity_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "workload-readiness-evidence.json"

            write_workload_report(output_path)

            artifact = output_path.read_text(encoding="utf-8")
            parsed = json.loads(artifact)
            self.assertEqual(parsed["schema_version"], 1)
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
                "subject",
            )
            lowered = artifact.lower()
            for fragment in forbidden_fragments:
                self.assertNotIn(fragment, lowered)


if __name__ == "__main__":
    unittest.main()
