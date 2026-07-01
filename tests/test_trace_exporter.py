import json
import tempfile
import unittest
from pathlib import Path

from fastapi import Response

import gateway.app as app_module
from gateway.audit import JsonlAuditSink
from gateway.trace_context import RequestTrace
from gateway.trace_exporter import JsonlTraceExporter, build_span_record


class TraceExporterTest(unittest.TestCase):
    def test_builds_otlp_shaped_span_without_sensitive_payload_fields(self) -> None:
        trace = RequestTrace(
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="1111111111111111",
            parent_span_id="00f067aa0ba902b7",
            sampled=True,
            source="remote",
        )

        record = build_span_record(
            trace,
            service_name="secure-gpu-inference-gateway",
            model_id="mission-summarizer",
            outcome="allowed",
            auth_method="demo-header",
            decision_reasons=(),
            latency_ms=7.25,
            started_at_unix_nano=1_700_000_000_000_000_000,
            ended_at_unix_nano=1_700_000_000_007_250_000,
            extra_attributes={
                "input": "SECRET_PROMPT",
                "output": "SECRET_OUTPUT",
                "reason": "SECRET_REASON",
                "principal_id": "analyst-1",
                "ai.gateway.estimated_input_tokens": 12,
                "ai.gateway.token_budget_limit": 8000,
            },
        )

        serialized = json.dumps(record, sort_keys=True)
        self.assertIn('"trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"', serialized)
        self.assertIn('"ai.gateway.outcome": "allowed"', serialized)
        self.assertIn('"ai.gateway.estimated_input_tokens": 12', serialized)
        self.assertIn('"ai.gateway.token_budget_limit": 8000', serialized)
        self.assertNotIn("SECRET_PROMPT", serialized)
        self.assertNotIn("SECRET_OUTPUT", serialized)
        self.assertNotIn("SECRET_REASON", serialized)
        self.assertNotIn("analyst-1", serialized)

    def test_app_trace_export_omits_prompt_output_reason_and_principal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "traces.jsonl"
            old_trace_exporter = app_module.trace_exporter
            old_audit_sink = app_module.audit_sink
            try:
                app_module.trace_exporter = JsonlTraceExporter(trace_path)
                app_module.audit_sink = JsonlAuditSink(str(Path(tmpdir) / "audit.jsonl"))
                response = app_module.infer(
                    "mission-summarizer",
                    app_module.InferenceRequest(
                        input="SECRET_PROMPT",
                        reason="SECRET_REASON",
                    ),
                    Response(),
                    x_principal_id="analyst-1",
                    authorization=None,
                    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
                )

                exported = trace_path.read_text(encoding="utf-8")
                self.assertIn("mission-summarizer", exported)
                self.assertNotIn("SECRET_PROMPT", exported)
                self.assertNotIn("SECRET_REASON", exported)
                self.assertNotIn(response.output, exported)
                self.assertNotIn("analyst-1", exported)
            finally:
                app_module.trace_exporter = old_trace_exporter
                app_module.audit_sink = old_audit_sink


if __name__ == "__main__":
    unittest.main()
