import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import gateway.app as app_module
from gateway.audit import JsonlAuditSink
from gateway.backend_adapter import BackendAdapterError, open_completion_response
from gateway.metrics import GatewayMetrics
from gateway.rate_limit import FixedWindowRateLimiter, FixedWindowTokenBudgetLimiter


STREAM_BODY = (
    b'data: {"choices":[{"text":"hello"}]}\n\n'
    b'data: {"usage":{"prompt_tokens":4,"completion_tokens":1}}\n\n'
    b"data: [DONE]\n\n"
)


def response_with_body(body: bytes, content_type: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": content_type, "x-request-id": "backend-request"},
        stream=httpx.ByteStream(body),
        request=httpx.Request("POST", "http://backend/v1/completions"),
    )


class CompletionProxyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_audit_sink = app_module.audit_sink
        self.old_metrics = app_module.metrics
        self.old_rate_limiter = app_module.rate_limiter
        self.old_token_budget_limiter = app_module.token_budget_limiter
        app_module.audit_sink = JsonlAuditSink(
            str(Path(self.tmpdir.name) / "audit.jsonl")
        )
        app_module.metrics = GatewayMetrics()
        app_module.rate_limiter = FixedWindowRateLimiter()
        app_module.token_budget_limiter = FixedWindowTokenBudgetLimiter()
        self.client = TestClient(app_module.app)

    def tearDown(self) -> None:
        self.client.close()
        app_module.audit_sink = self.old_audit_sink
        app_module.metrics = self.old_metrics
        app_module.rate_limiter = self.old_rate_limiter
        app_module.token_budget_limiter = self.old_token_budget_limiter
        self.tmpdir.cleanup()

    def test_streaming_completion_preserves_bytes_and_full_control_path(self) -> None:
        captured = {}

        def open_response(payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            return response_with_body(STREAM_BODY, "text/event-stream")

        payload = {
            "model": "benchmark-echo",
            "prompt": "deterministic prompt",
            "repetition_penalty": 1.0,
            "max_tokens": 16,
            "logprobs": None,
            "stream": True,
            "stream_options": {"include_usage": True},
            "ignore_eos": True,
        }
        with patch.object(app_module, "open_completion_response", open_response):
            response = self.client.post(
                "/v1/completions",
                json=payload,
                headers={
                    "X-Principal-Id": "analyst-1",
                    "X-Request-Id": "client-request",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, STREAM_BODY)
        self.assertEqual(captured["payload"], payload)
        self.assertEqual(captured["kwargs"]["request_id"], "client-request")
        self.assertEqual(response.headers["x-request-id"], "backend-request")
        self.assertIn("gateway-admission", response.headers["server-timing"])
        self.assertIn("traceparent", response.headers)

        audit_rows = [
            json.loads(line)
            for line in (Path(self.tmpdir.name) / "audit.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0]["model_id"], "benchmark-echo")
        self.assertEqual(audit_rows[0]["decision_reasons"], ["policy allow"])
        self.assertNotIn("deterministic prompt", json.dumps(audit_rows))

        metrics = app_module.metrics.render_prometheus(app_module.MODEL_POLICIES)
        self.assertIn(
            'security_gateway_requests_total{model_id="benchmark-echo",outcome="allowed"} 1',
            metrics,
        )
        self.assertIn("security_gateway_limiter_latency_seconds_count", metrics)

    def test_non_streaming_completion_preserves_backend_json(self) -> None:
        body = b'{"choices":[{"text":"hello"}],"usage":{"completion_tokens":1}}'
        with patch.object(
            app_module,
            "open_completion_response",
            return_value=response_with_body(body, "application/json"),
        ):
            response = self.client.post(
                "/v1/completions",
                json={
                    "model": "benchmark-echo",
                    "prompt": "prompt",
                    "max_tokens": 1,
                    "stream": False,
                },
                headers={"X-Principal-Id": "analyst-1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, body)

    def test_denies_unknown_principal_before_opening_backend(self) -> None:
        with patch.object(app_module, "open_completion_response") as opener:
            response = self.client.post(
                "/v1/completions",
                json={
                    "model": "benchmark-echo",
                    "prompt": "prompt",
                    "max_tokens": 1,
                    "stream": True,
                },
            )

        self.assertEqual(response.status_code, 403)
        opener.assert_not_called()

    def test_returns_generic_backend_error(self) -> None:
        with patch.object(
            app_module,
            "open_completion_response",
            side_effect=BackendAdapterError("secret host"),
        ):
            response = self.client.post(
                "/v1/completions",
                json={
                    "model": "benchmark-echo",
                    "prompt": "prompt",
                    "max_tokens": 1,
                    "stream": True,
                },
                headers={"X-Principal-Id": "analyst-1"},
            )

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret host", response.text)


class CompletionBackendClientTest(unittest.TestCase):
    def test_pooled_client_forwards_payload_and_safe_headers(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["request"] = request
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=STREAM_BODY,
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            response = open_completion_response(
                {"model": "benchmark-echo", "prompt": "prompt", "stream": True},
                endpoint="http://backend:8000/v1",
                api_key="backend-secret",
                timeout_seconds=2,
                request_id="request-1",
                client=client,
            )
            body = response.read()
            response.close()

        request = captured["request"]
        self.assertEqual(request.url, "http://backend:8000/v1/completions")
        self.assertEqual(request.headers["authorization"], "Bearer backend-secret")
        self.assertEqual(request.headers["x-request-id"], "request-1")
        self.assertEqual(body, STREAM_BODY)


if __name__ == "__main__":
    unittest.main()
