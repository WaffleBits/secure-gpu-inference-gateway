import json
import unittest
from pathlib import Path

from bench.run import (
    build_schedule,
    build_vllm_command,
    extract_raw_samples,
    limiter_latency_delta,
    load_config,
    parse_prometheus,
    sanitize_config,
    smoke_config,
    validate_config,
)


ROOT = Path(__file__).resolve().parents[1]


class BenchmarkRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(ROOT / "bench" / "config.example.json")

    def test_schedule_pairs_paths_and_alternates_order(self) -> None:
        config = smoke_config(self.config)
        config["repetitions"] = 2
        schedule = build_schedule(config)

        self.assertEqual([row["path"] for row in schedule[:2]], ["direct", "gateway"])
        self.assertEqual([row["path"] for row in schedule[2:4]], ["gateway", "direct"])
        for offset in (0, 2):
            self.assertEqual(schedule[offset]["seed"], schedule[offset + 1]["seed"])
            self.assertEqual(
                schedule[offset]["request_count"], schedule[offset + 1]["request_count"]
            )

    def test_command_uses_official_detailed_streaming_benchmark(self) -> None:
        config = smoke_config(self.config)
        condition = build_schedule(config)[1]
        command = build_vllm_command(config, condition, Path("results"), "run.json")

        joined = " ".join(command)
        self.assertIn("--backend openai", joined)
        self.assertIn("--endpoint /v1/completions", joined)
        self.assertIn("--save-detailed", command)
        self.assertIn("--ignore-eos", command)
        self.assertIn("X-Principal-Id=analyst-1", command)
        self.assertIn("ttft,tpot,itl,e2el", command)

    def test_detailed_result_becomes_payload_free_raw_samples(self) -> None:
        condition = build_schedule(smoke_config(self.config))[0]
        result = {
            "input_lens": [32, 32],
            "output_lens": [3, 0],
            "ttfts": [0.01, 0.0],
            "itls": [[0.02, 0.03], []],
            "start_times": [1.0, 2.0],
            "errors": ["", "HTTP 500 at http://secret-backend/v1"],
            "generated_texts": ["private output", ""],
        }

        rows = extract_raw_samples(result, condition, "condition")

        self.assertEqual(rows[0]["total_latency_ms"], 60.0)
        self.assertEqual(rows[0]["tpot_ms"], 25.0)
        self.assertEqual(rows[0]["inter_token_latency_ms"], [20.0, 30.0])
        self.assertFalse(rows[1]["success"])
        self.assertIn("<endpoint>", rows[1]["error"])
        self.assertNotIn("private output", json.dumps(rows))

    def test_limiter_histogram_delta_reports_mean_and_conservative_bounds(self) -> None:
        before = parse_prometheus(
            "\n".join(
                [
                    'security_gateway_limiter_latency_seconds_bucket{backend="redis",le="0.001",limiter="requests"} 2',
                    'security_gateway_limiter_latency_seconds_bucket{backend="redis",le="0.005",limiter="requests"} 3',
                    'security_gateway_limiter_latency_seconds_bucket{backend="redis",le="+Inf",limiter="requests"} 3',
                    'security_gateway_limiter_latency_seconds_count{backend="redis",limiter="requests"} 3',
                    'security_gateway_limiter_latency_seconds_sum{backend="redis",limiter="requests"} 0.004',
                ]
            )
        )
        after = parse_prometheus(
            "\n".join(
                [
                    'security_gateway_limiter_latency_seconds_bucket{backend="redis",le="0.001",limiter="requests"} 4',
                    'security_gateway_limiter_latency_seconds_bucket{backend="redis",le="0.005",limiter="requests"} 7',
                    'security_gateway_limiter_latency_seconds_bucket{backend="redis",le="+Inf",limiter="requests"} 7',
                    'security_gateway_limiter_latency_seconds_count{backend="redis",limiter="requests"} 7',
                    'security_gateway_limiter_latency_seconds_sum{backend="redis",limiter="requests"} 0.016',
                ]
            )
        )

        result = limiter_latency_delta(before, after)

        self.assertEqual(result["requests"]["count"], 4)
        self.assertEqual(result["requests"]["mean_ms"], 3.0)
        self.assertEqual(result["requests"]["p50_upper_bound_ms"], 1.0)
        self.assertEqual(result["requests"]["p95_upper_bound_ms"], 5.0)

    def test_sensitive_header_is_redacted_from_resolved_config(self) -> None:
        self.config["paths"]["gateway"]["headers"]["Authorization"] = "secret"
        sanitized = sanitize_config(self.config)
        self.assertEqual(
            sanitized["paths"]["gateway"]["headers"]["Authorization"],
            "<redacted>",
        )

    def test_runtime_capture_configuration_is_validated(self) -> None:
        self.config["model"]["vllm_environment"] = {"CUDA_VISIBLE_DEVICES": "0"}
        with self.assertRaisesRegex(ValueError, "keys must start with VLLM_"):
            validate_config(self.config)

        self.config["model"]["vllm_environment"] = {}
        self.config["python_environments"] = {"gateway": []}
        with self.assertRaisesRegex(ValueError, "non-empty argument lists"):
            validate_config(self.config)


if __name__ == "__main__":
    unittest.main()
