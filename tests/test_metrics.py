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

        rendered = metrics.render_prometheus(MODEL_POLICIES)

        self.assertIn(
            'security_gateway_model_policy_info{model_id="mission-summarizer",requires_reason="true",sensitivity="standard"} 1',
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


if __name__ == "__main__":
    unittest.main()
