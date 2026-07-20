import json
import unittest

from gateway.telemetry_snapshot import build_snapshot, parse_labels, parse_prometheus


METRICS = """\
# HELP security_gateway_requests_total Inference gateway requests.
security_gateway_requests_total{model_id="mission-summarizer",outcome="allowed"} 3
security_gateway_requests_total{model_id="mission-summarizer",outcome="policy_denied"} 1
security_gateway_input_tokens_total{model_id="mission-summarizer",outcome="allowed"} 30
security_gateway_input_tokens_total{model_id="mission-summarizer",outcome="policy_denied"} 4
security_gateway_inference_latency_seconds_bucket{model_id="mission-summarizer",le="0.025"} 1
security_gateway_inference_latency_seconds_bucket{model_id="mission-summarizer",le="0.05"} 3
security_gateway_inference_latency_seconds_bucket{model_id="mission-summarizer",le="0.1"} 3
security_gateway_inference_latency_seconds_bucket{model_id="mission-summarizer",le="+Inf"} 3
security_gateway_inference_latency_seconds_count{model_id="mission-summarizer"} 3
security_gateway_inference_latency_seconds_sum{model_id="mission-summarizer"} 0.105
"""


class TelemetrySnapshotTests(unittest.TestCase):
    def test_parser_handles_escaped_labels_and_ignores_unknown_metrics(self):
        self.assertEqual(parse_labels('model_id="a\\\"b",outcome="allowed"'), {
            "model_id": 'a"b',
            "outcome": "allowed",
        })
        samples = parse_prometheus(METRICS + "python_info 1\n")
        self.assertEqual(len(samples), 10)

    def test_snapshot_correlates_counters_histograms_and_probe(self):
        report = build_snapshot(
            METRICS,
            {
                "status": "pass",
                "backend_protocol": "openai-compatible-completions",
                "model_id": "mission-summarizer",
                "attempted_requests": 2,
                "successful_requests": 2,
                "failed_requests": 0,
                "success_rate": 1.0,
                "latency_ms": {"p95": 18.0},
                "total_tokens_reported": 14,
                "evidence_scope": ["aggregate latency"],
                "endpoint": "should never be copied",
            },
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["gateway"]["requests"]["total"], 4)
        self.assertEqual(report["gateway"]["requests"]["allowed_rate"], 0.75)
        self.assertEqual(report["gateway"]["latency_ms"]["mission-summarizer"]["mean_ms"], 35.0)
        self.assertEqual(
            report["gateway"]["latency_ms"]["mission-summarizer"]["p95_upper_bound_ms"],
            50.0,
        )
        self.assertNotIn("should never be copied", json.dumps(report).lower())

    def test_probe_hold_holds_snapshot(self):
        report = build_snapshot(METRICS, {"status": "hold", "success_rate": 0.5})
        self.assertEqual(report["status"], "hold")
        self.assertEqual(report["release_gates"][-1]["status"], "hold")


if __name__ == "__main__":
    unittest.main()
