import json
import tempfile
import unittest
from pathlib import Path

from gateway.trace_context import RequestTrace
from gateway.trace_exporter import (
    build_otlp_traces_payload,
    build_span_record,
    write_otlp_payload,
)


class OtlpExportTest(unittest.TestCase):
    def test_builds_otlp_http_payload_from_sanitized_span(self) -> None:
        record = build_span_record(
            RequestTrace(
                trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                span_id="1111111111111111",
                parent_span_id="00f067aa0ba902b7",
                sampled=True,
                source="remote",
            ),
            service_name="secure-gpu-inference-gateway",
            model_id="mission-summarizer",
            outcome="allowed",
            auth_method="demo-header",
            decision_reasons=(),
            latency_ms=7.25,
            started_at_unix_nano=1_700_000_000_000_000_000,
            ended_at_unix_nano=1_700_000_000_007_250_000,
            extra_attributes={
                "ai.gateway.estimated_input_tokens": 12,
                "ai.gateway.token_budget_limit": 8000,
                "prompt": "SECRET_PROMPT",
                "principal_id": "analyst-1",
            },
        )

        payload = build_otlp_traces_payload((record,))
        serialized = json.dumps(payload, sort_keys=True)

        self.assertIn('"resourceSpans"', serialized)
        self.assertIn('"traceId": "4bf92f3577b34da6a3ce929d0e0e4736"', serialized)
        self.assertIn('"kind": 2', serialized)
        self.assertIn('"intValue": "12"', serialized)
        self.assertIn('"doubleValue": 7.25', serialized)
        self.assertNotIn("SECRET_PROMPT", serialized)
        self.assertNotIn("analyst-1", serialized)

    def test_writes_payload_artifact_from_jsonl_without_sensitive_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "trace.jsonl"
            output_path = Path(tmpdir) / "otlp.json"
            trace_path.write_text(
                json.dumps(
                    build_span_record(
                        RequestTrace(
                            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                            span_id="1111111111111111",
                            parent_span_id=None,
                            sampled=True,
                            source="generated",
                        ),
                        service_name="secure-gpu-inference-gateway",
                        model_id="benchmark-echo",
                        outcome="policy_denied",
                        auth_method="demo-header",
                        decision_reasons=("missing required role",),
                        latency_ms=None,
                        started_at_unix_nano=1,
                        ended_at_unix_nano=2,
                        extra_attributes={"output": "SECRET_OUTPUT"},
                    ),
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = write_otlp_payload(trace_path, output_path)
            artifact = output_path.read_text(encoding="utf-8")

            self.assertEqual(payload["resourceSpans"], json.loads(artifact)["resourceSpans"])
            self.assertNotIn("SECRET_OUTPUT", artifact)
            self.assertIn('"message": "policy_denied"', artifact)


if __name__ == "__main__":
    unittest.main()
