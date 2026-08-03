import unittest

from gateway.metrics import GatewayMetrics
from gateway.registry import MODEL_POLICIES


class MetricsTest(unittest.TestCase):
    def test_renders_policy_request_denial_and_latency_metrics(self) -> None:
        metrics = GatewayMetrics(latency_buckets=(0.1, 0.5))

        metrics.record_request("mission-summarizer", "allowed")
        metrics.observe_latency("mission-summarizer", 0.15)
        metrics.record_request(
            "threat-triage",
            "policy_denied",
            ("principal lacks an allowed role for this model",),
        )
        metrics.record_auth_event("jwt", "accepted")
        metrics.record_input_tokens("mission-summarizer", "allowed", 42)
        metrics.observe_limiter_latency("requests", "redis", 0.0004)

        rendered = metrics.render_prometheus(MODEL_POLICIES)

        self.assertIn(
            'security_gateway_model_policy_info{input_tokens_per_minute="8000",model_id="mission-summarizer",requests_per_minute="30",requires_reason="true",sensitivity="standard"} 1',
            rendered,
        )
        self.assertIn(
            'security_gateway_requests_total{model_id="mission-summarizer",outcome="allowed"} 1',
            rendered,
        )
        self.assertIn(
            'security_gateway_denials_total{model_id="threat-triage",reason="principal lacks an allowed role for this model"} 1',
            rendered,
        )
        self.assertIn(
            'security_gateway_auth_events_total{auth_method="jwt",outcome="accepted"} 1',
            rendered,
        )
        self.assertIn(
            'security_gateway_input_tokens_total{model_id="mission-summarizer",outcome="allowed"} 42',
            rendered,
        )
        self.assertIn(
            'security_gateway_inference_latency_seconds_bucket{le="0.1",model_id="mission-summarizer"} 0',
            rendered,
        )
        self.assertIn(
            'security_gateway_inference_latency_seconds_bucket{le="0.5",model_id="mission-summarizer"} 1',
            rendered,
        )
        self.assertIn(
            'security_gateway_inference_latency_seconds_count{model_id="mission-summarizer"} 1',
            rendered,
        )
        self.assertIn(
            'security_gateway_inference_latency_seconds_sum{model_id="mission-summarizer"} 0.15',
            rendered,
        )
        self.assertIn(
            'security_gateway_limiter_latency_seconds_bucket{backend="redis",le="0.0005",limiter="requests"} 1',
            rendered,
        )
        self.assertIn(
            'security_gateway_limiter_latency_seconds_count{backend="redis",limiter="requests"} 1',
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
