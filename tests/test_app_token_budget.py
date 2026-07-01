import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fastapi import HTTPException, Response

import gateway.app as app_module
from gateway.audit import JsonlAuditSink
from gateway.metrics import GatewayMetrics
from gateway.rate_limit import FixedWindowRateLimiter, FixedWindowTokenBudgetLimiter


class AppTokenBudgetTest(unittest.TestCase):
    def test_app_blocks_when_principal_model_token_budget_is_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.jsonl"
            old_policy = app_module.MODEL_POLICIES["benchmark-echo"]
            old_audit_sink = app_module.audit_sink
            old_metrics = app_module.metrics
            old_rate_limiter = app_module.rate_limiter
            old_token_budget_limiter = app_module.token_budget_limiter
            try:
                app_module.MODEL_POLICIES["benchmark-echo"] = replace(
                    old_policy,
                    input_tokens_per_minute=2,
                )
                app_module.audit_sink = JsonlAuditSink(str(audit_path))
                app_module.metrics = GatewayMetrics()
                app_module.rate_limiter = FixedWindowRateLimiter()
                app_module.token_budget_limiter = FixedWindowTokenBudgetLimiter()

                first_response = app_module.infer(
                    "benchmark-echo",
                    app_module.InferenceRequest(input="abcdefgh"),
                    Response(),
                    x_principal_id="analyst-1",
                    authorization=None,
                    traceparent=None,
                )

                self.assertEqual(first_response.audit["estimated_input_tokens"], 2)

                with self.assertRaises(HTTPException) as raised:
                    app_module.infer(
                        "benchmark-echo",
                        app_module.InferenceRequest(input="abcdefgh"),
                        Response(),
                        x_principal_id="analyst-1",
                        authorization=None,
                        traceparent=None,
                    )

                self.assertEqual(raised.exception.status_code, 429)
                self.assertEqual(raised.exception.detail["reason"], "token budget exceeded")
                self.assertEqual(raised.exception.detail["estimated_input_tokens"], 2)
                self.assertEqual(raised.exception.detail["input_tokens_per_minute"], 2)

                audit_events = [
                    json.loads(line)
                    for line in audit_path.read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual(audit_events[-1]["decision_reasons"], ["token budget exceeded"])
                self.assertEqual(audit_events[-1]["estimated_input_tokens"], 2)
                self.assertEqual(audit_events[-1]["token_budget_limit"], 2)

                rendered_metrics = app_module.metrics.render_prometheus(
                    app_module.MODEL_POLICIES
                )
                self.assertIn(
                    'security_gateway_requests_total{model_id="benchmark-echo",outcome="token_budget_limited"} 1',
                    rendered_metrics,
                )
                self.assertIn(
                    'security_gateway_input_tokens_total{model_id="benchmark-echo",outcome="token_budget_limited"} 2',
                    rendered_metrics,
                )
            finally:
                app_module.MODEL_POLICIES["benchmark-echo"] = old_policy
                app_module.audit_sink = old_audit_sink
                app_module.metrics = old_metrics
                app_module.rate_limiter = old_rate_limiter
                app_module.token_budget_limiter = old_token_budget_limiter


if __name__ == "__main__":
    unittest.main()
