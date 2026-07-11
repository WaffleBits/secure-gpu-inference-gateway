import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from fastapi import HTTPException, Response

import gateway.app as app_module
from gateway.audit import JsonlAuditSink
from gateway.metrics import GatewayMetrics
from gateway.backend_adapter import (
    BackendAdapterError,
    completion_url,
    extract_completion,
    run_configured_inference,
    run_openai_compatible_inference,
)


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class BackendAdapterTest(unittest.TestCase):
    def test_completion_url_accepts_base_or_full_endpoint(self) -> None:
        self.assertEqual(
            completion_url("http://vllm:8000"),
            "http://vllm:8000/v1/completions",
        )
        self.assertEqual(
            completion_url("http://sglang:30000/v1"),
            "http://sglang:30000/v1/completions",
        )
        self.assertEqual(
            completion_url("http://backend/v1/completions"),
            "http://backend/v1/completions",
        )

    def test_posts_prompt_and_extracts_completion_usage(self) -> None:
        calls: list[tuple[object, float]] = []

        def opener(request: object, timeout: float) -> FakeResponse:
            calls.append((request, timeout))
            return FakeResponse(
                {
                    "model": "served-model",
                    "choices": [{"text": "safe response"}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                }
            )

        result = run_openai_compatible_inference(
            "mission-summarizer",
            "synthetic prompt",
            endpoint="http://vllm:8000/v1",
            api_key="secret-token",
            timeout_seconds=2.5,
            opener=opener,
        )

        request = calls[0][0]
        self.assertEqual(calls[0][1], 2.5)
        self.assertEqual(request.full_url, "http://vllm:8000/v1/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
        self.assertEqual(json.loads(request.data), {
            "model": "mission-summarizer",
            "prompt": "synthetic prompt",
            "max_tokens": 256,
            "stream": False,
        })
        self.assertEqual(result["model_id"], "served-model")
        self.assertEqual(result["output"], "safe response")
        self.assertEqual(result["usage"], {"prompt_tokens": 4, "completion_tokens": 2})

    def test_extracts_chat_completion_shape(self) -> None:
        self.assertEqual(
            extract_completion(
                {"choices": [{"message": {"content": "chat response"}}]}
            ),
            ("chat response", None),
        )

    def test_rejects_invalid_backend_payload(self) -> None:
        with self.assertRaises(BackendAdapterError):
            extract_completion({"choices": []})

        def opener(request: object, timeout: float) -> FakeResponse:
            return FakeResponse({"choices": [{"finish_reason": "stop"}]})

        with self.assertRaises(BackendAdapterError):
            run_openai_compatible_inference(
                "model",
                "prompt",
                endpoint="http://backend/v1",
                opener=opener,
            )

    def test_translates_transport_failure_without_exposing_details(self) -> None:
        def opener(request: object, timeout: float) -> FakeResponse:
            raise URLError("secret-hostname")

        with self.assertRaisesRegex(BackendAdapterError, "request failed"):
            run_openai_compatible_inference(
                "model",
                "prompt",
                endpoint="http://backend/v1",
                opener=opener,
            )

    def test_mock_backend_remains_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = run_configured_inference("benchmark-echo", "prompt")
        self.assertEqual(result["backend"], "mock-gpu")

    def test_app_returns_generic_502_when_backend_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_audit_sink = app_module.audit_sink
            old_metrics = app_module.metrics
            try:
                app_module.audit_sink = JsonlAuditSink(str(Path(tmpdir) / "audit.jsonl"))
                app_module.metrics = GatewayMetrics()
                with patch.object(
                    app_module,
                    "run_configured_inference",
                    side_effect=BackendAdapterError("secret backend hostname"),
                ):
                    with self.assertRaises(HTTPException) as raised:
                        app_module.infer(
                            "benchmark-echo",
                            app_module.InferenceRequest(input="prompt"),
                            Response(),
                            x_principal_id="analyst-1",
                            authorization=None,
                            traceparent=None,
                        )

                self.assertEqual(raised.exception.status_code, 502)
                self.assertEqual(
                    raised.exception.detail["reason"],
                    "inference backend unavailable",
                )
                self.assertNotIn("secret backend hostname", str(raised.exception.detail))
                rendered_metrics = app_module.metrics.render_prometheus(
                    app_module.MODEL_POLICIES
                )
                self.assertIn(
                    'security_gateway_requests_total{model_id="benchmark-echo",outcome="backend_error"} 1',
                    rendered_metrics,
                )
            finally:
                app_module.audit_sink = old_audit_sink
                app_module.metrics = old_metrics


if __name__ == "__main__":
    unittest.main()
