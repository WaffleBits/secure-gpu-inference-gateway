import unittest

from gateway.backend_adapter import BackendAdapterError
from gateway.backend_probe import percentile, run_probe


class BackendProbeTests(unittest.TestCase):
    def test_percentile_uses_nearest_rank_and_empty_is_null(self):
        self.assertEqual(percentile([30, 10, 20, 40], 50), 20)
        self.assertEqual(percentile([30, 10, 20, 40], 95), 40)
        self.assertIsNone(percentile([], 50))

    def test_probe_returns_aggregate_pass_evidence_without_payloads(self):
        calls = []

        def request_fn(model_id, user_input, **kwargs):
            calls.append((model_id, user_input, kwargs))
            return {
                "latency_ms": 12.5 + len(calls),
                "usage": {"total_tokens": 7},
            }

        report = run_probe(
            "readiness-model",
            ["first private prompt", "second private prompt"],
            endpoint="http://127.0.0.1:8000/v1",
            api_key="secret-key",
            request_fn=request_fn,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["successful_requests"], 2)
        self.assertEqual(report["total_tokens_reported"], 14)
        self.assertNotIn("private prompt", json_text(report))
        self.assertNotIn("secret-key", json_text(report))
        self.assertEqual(calls[0][2]["endpoint"], "http://127.0.0.1:8000/v1")

    def test_probe_holds_when_backend_requests_fail(self):
        def request_fn(*args, **kwargs):
            raise BackendAdapterError("private backend detail")

        report = run_probe(
            "readiness-model",
            ["probe"],
            endpoint="http://backend/v1",
            request_fn=request_fn,
        )

        self.assertEqual(report["status"], "hold")
        self.assertEqual(report["failed_requests"], 1)
        self.assertIsNone(report["latency_ms"]["p95"])


def json_text(value):
    import json

    return json.dumps(value)


if __name__ == "__main__":
    unittest.main()
