import json
import tempfile
import unittest
from pathlib import Path

from gateway.distributed_limiter import (
    REDIS_FIXED_WINDOW_LUA,
    build_distributed_limiter_report,
    stable_sha256,
    write_distributed_limiter_report,
)
from gateway.registry import MODEL_POLICIES


class DistributedLimiterTest(unittest.TestCase):
    def test_report_covers_request_and_token_budgets_for_each_model(self) -> None:
        report = build_distributed_limiter_report()

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["readiness_status"], "pass")
        self.assertEqual(report["rule_count"], len(MODEL_POLICIES) * 2)

        observed = {
            (rule["model_id"], rule["budget_type"])
            for rule in report["rules"]
        }
        expected = {
            (model_id, budget_type)
            for model_id in MODEL_POLICIES
            for budget_type in ("request_count", "estimated_input_tokens")
        }
        self.assertEqual(observed, expected)

    def test_report_records_atomic_redis_script_shape(self) -> None:
        report = build_distributed_limiter_report()
        gate_by_name = {gate["name"]: gate for gate in report["release_gates"]}

        self.assertEqual(
            report["redis_atomic_script"]["sha256"],
            stable_sha256(REDIS_FIXED_WINDOW_LUA),
        )
        self.assertEqual(gate_by_name["redis_atomic_window_shape"]["status"], "pass")
        self.assertIn("INCRBY", REDIS_FIXED_WINDOW_LUA)
        self.assertIn("PEXPIRE", REDIS_FIXED_WINDOW_LUA)

    def test_written_artifact_excludes_sensitive_payload_and_identity_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "distributed-limiter-evidence.json"

            write_distributed_limiter_report(output_path)

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
