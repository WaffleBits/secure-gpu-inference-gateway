import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import gateway.app as app_module
from gateway.audit import DenialAuditBudget, JsonlAuditSink
from gateway.metrics import GatewayMetrics
from gateway.rate_limit import FixedWindowRateLimiter, FixedWindowTokenBudgetLimiter

BODY = {"input": "hello"}


class AnonymousAbuseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.tmpdir.name) / "audit.jsonl"
        self.saved = {
            name: getattr(app_module, name)
            for name in (
                "audit_sink",
                "metrics",
                "rate_limiter",
                "token_budget_limiter",
                "auth_settings",
                "anonymous_denial_budget",
            )
        }
        app_module.audit_sink = JsonlAuditSink(str(self.audit_path))
        app_module.metrics = GatewayMetrics()
        app_module.rate_limiter = FixedWindowRateLimiter()
        app_module.token_budget_limiter = FixedWindowTokenBudgetLimiter()
        app_module.auth_settings = replace(
            app_module.auth_settings, allow_demo_principals=True
        )
        app_module.anonymous_denial_budget = DenialAuditBudget(5)
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        for name, value in self.saved.items():
            setattr(app_module, name, value)
        self.tmpdir.cleanup()

    def audit_lines(self) -> list[dict[str, object]]:
        if not self.audit_path.exists():
            return []
        return [json.loads(line) for line in self.audit_path.read_text().splitlines()]

    def test_unknown_models_share_one_metric_label(self) -> None:
        for index in range(50):
            self.client.post(f"/v1/infer/junk-{index}", json=BODY)

        body = self.client.get("/metrics").text

        self.assertNotIn("junk-", body)
        self.assertIn(
            'security_gateway_requests_total{model_id="unknown",outcome="policy_denied"} 50',
            body,
        )

    def test_completion_path_shares_the_same_label(self) -> None:
        self.client.post("/v1/completions", json={"model": "junk-x", "prompt": "hi"})

        self.assertNotIn("junk-x", self.client.get("/metrics").text)

    def test_anonymous_denials_stop_reaching_the_audit_log(self) -> None:
        for index in range(20):
            response = self.client.post(f"/v1/infer/junk-{index}", json=BODY)
            self.assertEqual(response.status_code, 403)

        self.assertEqual(len(self.audit_lines()), 5)
        self.assertIn(
            "security_gateway_audit_suppressed_total 15",
            self.client.get("/metrics").text,
        )

    def test_authenticated_denials_are_always_audited(self) -> None:
        headers = {"X-Principal-Id": "analyst-1"}
        for index in range(20):
            response = self.client.post(
                f"/v1/infer/junk-{index}", json=BODY, headers=headers
            )
            self.assertEqual(response.status_code, 403)

        self.assertEqual(len(self.audit_lines()), 20)

    def test_oversized_model_id_is_rejected_before_any_record(self) -> None:
        response = self.client.post(f"/v1/infer/{'x' * 201}", json=BODY)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.audit_lines(), [])

    def test_budget_refills_each_minute(self) -> None:
        now = [0.0]
        budget = DenialAuditBudget(1, clock=lambda: now[0])

        self.assertTrue(budget.allow())
        self.assertFalse(budget.allow())
        now[0] = 61.0
        self.assertTrue(budget.allow())

    def test_model_list_requires_a_caller(self) -> None:
        self.assertEqual(self.client.get("/v1/models").status_code, 401)
        allowed = self.client.get("/v1/models", headers={"X-Principal-Id": "analyst-1"})
        self.assertEqual(allowed.status_code, 200)

    def test_api_docs_are_off_by_default(self) -> None:
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_metrics_token_is_enforced_when_set(self) -> None:
        with patch.dict(os.environ, {"METRICS_BEARER_TOKEN": "scrape-token"}):
            self.assertEqual(self.client.get("/metrics").status_code, 401)
            wrong = self.client.get("/metrics", headers={"Authorization": "Bearer no"})
            self.assertEqual(wrong.status_code, 401)
            right = self.client.get(
                "/metrics", headers={"Authorization": "Bearer scrape-token"}
            )
            self.assertEqual(right.status_code, 200)


if __name__ == "__main__":
    unittest.main()
