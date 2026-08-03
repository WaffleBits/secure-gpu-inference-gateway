import json
import tempfile
import unittest
from pathlib import Path

from bench.analyze import analyze_run, build_headline, combine_limiter_latency


class BenchmarkAnalysisTest(unittest.TestCase):
    def test_builds_comparison_report_plots_and_predeclared_headline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            config = {
                "workloads": [
                    {"name": "medium", "input_tokens": 256, "output_tokens": 128}
                ],
                "concurrency": [8],
                "repetitions": 2,
                "headline": {"workload": "medium", "concurrency": 8},
            }
            (run_dir / "config.resolved.json").write_text(
                json.dumps(config), encoding="utf-8"
            )
            (run_dir / "environment.json").write_text(
                json.dumps(
                    {
                        "git": {"commit": "abc", "dirty": False},
                        "operating_system": {"platform": "test-os"},
                        "cpu": {"model": "test-cpu"},
                        "system_ram_bytes": 1024,
                        "gpu": {
                            "devices": [
                                {
                                    "name": "test-gpu",
                                    "memory_total_mib": 16000,
                                    "driver_version": "1",
                                }
                            ],
                            "cuda_version": "12.8",
                        },
                        "software": {"vllm": "test"},
                        "python": {"version": "3.12"},
                        "model": {
                            "id": "model",
                            "revision": "rev",
                            "dtype": "bf16",
                            "quantization": None,
                        },
                        "redis": {"version": "8"},
                    }
                ),
                encoding="utf-8",
            )

            samples = []
            conditions = []
            for repetition in range(2):
                for path, delta, duration in (
                    ("direct", 0.0, 10.0),
                    ("gateway", 3.0, 10.5),
                ):
                    condition_id = f"r{repetition}-{path}"
                    conditions.append(
                        {
                            "condition_id": condition_id,
                            "status": "completed",
                            "path": path,
                            "workload": "medium",
                            "concurrency": 8,
                            "repetition": repetition,
                            "duration_seconds": duration,
                            "completed_requests": 32,
                            "failed_requests": 0,
                            "total_input_tokens": 8192,
                            "total_output_tokens": 4096,
                            "request_throughput": 32 / duration,
                            "output_token_throughput": 4096 / duration,
                            "gateway_limiter_latency": None,
                        }
                    )
                    for index in range(32):
                        latency = 100.0 + index + delta
                        samples.append(
                            {
                                "condition_id": condition_id,
                                "path": path,
                                "workload": "medium",
                                "concurrency": 8,
                                "repetition": repetition,
                                "request_index": index,
                                "success": True,
                                "prompt_tokens": 256,
                                "requested_output_tokens": 128,
                                "completion_tokens": 128,
                                "ttft_ms": 20.0 + delta,
                                "total_latency_ms": latency,
                                "tpot_ms": 1.0,
                                "inter_token_latency_ms": [1.0, 1.1],
                            }
                        )
            write_jsonl(run_dir / "raw_samples.jsonl", samples)
            write_jsonl(run_dir / "conditions.jsonl", conditions)
            (run_dir / "resource_samples.jsonl").write_text("", encoding="utf-8")

            summary = analyze_run(run_dir, config)

            self.assertEqual(
                summary["comparisons"][0]["gateway_overhead_ms"]["p50"], 3.0
            )
            self.assertEqual(
                summary["comparisons"][0]["paired_runs"][0][
                    "p50_latency_difference_ms"
                ],
                3.0,
            )
            self.assertTrue(summary["comparisons"][0]["workload_equivalent"])
            self.assertEqual(summary["headline"]["status"], "supported")
            self.assertEqual(
                summary["headline"]["latency_claim"]["status"], "supported"
            )
            self.assertTrue((run_dir / "report.md").exists())
            self.assertTrue((run_dir / "summary.json").exists())
            self.assertTrue(
                (run_dir / "plots" / "gateway-overhead-vs-concurrency.svg").exists()
            )
            report = (run_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("added 3.00 ms p50", report)
            self.assertIn("Paired repetitions", report)

    def test_negative_paired_latency_differences_hold_causal_latency_claim(
        self,
    ) -> None:
        paired_runs = [
            {
                "repetition": repetition,
                "p50_latency_difference_ms": difference,
                "throughput_retained_percent": retained,
            }
            for repetition, difference, retained in (
                (0, -10.0, 101.0),
                (1, -4.0, 103.0),
                (2, -2.0, 99.0),
            )
        ]
        comparison = {
            "workload": "medium",
            "concurrency": 8,
            "workload_equivalent": True,
            "direct": {
                "repetitions": 3,
                "completed_requests": 60,
                "failed_requests": 0,
            },
            "gateway": {
                "repetitions": 3,
                "completed_requests": 60,
                "failed_requests": 0,
            },
            "gateway_overhead_ms": {"p50": -4.0},
            "throughput_retained_percent": 101.0,
            "paired_runs": paired_runs,
            "paired_run_variation": {
                "p50_latency_difference_ms": {"min": -10.0, "max": -2.0},
                "throughput_retained_percent": {"min": 99.0, "max": 103.0},
            },
        }

        headline = build_headline(
            [comparison], {"headline": {"workload": "medium", "concurrency": 8}}
        )

        self.assertEqual(headline["status"], "supported")
        self.assertEqual(headline["latency_claim"]["status"], "held")
        self.assertIn(
            "no positive gateway-added latency or speedup claim", headline["text"]
        )

    def test_limiter_summary_does_not_understate_overflowed_percentiles(self) -> None:
        combined = combine_limiter_latency(
            [
                {
                    "gateway_limiter_latency": {
                        "requests": {
                            "backend": "redis",
                            "count": 10,
                            "mean_ms": 10.0,
                            "p50_upper_bound_ms": 50.0,
                            "p95_upper_bound_ms": None,
                            "p99_upper_bound_ms": None,
                        }
                    }
                },
                {
                    "gateway_limiter_latency": {
                        "requests": {
                            "backend": "redis",
                            "count": 10,
                            "mean_ms": 5.0,
                            "p50_upper_bound_ms": 5.0,
                            "p95_upper_bound_ms": 25.0,
                            "p99_upper_bound_ms": 50.0,
                        }
                    }
                },
            ]
        )

        self.assertEqual(combined["requests"]["p50_upper_bound_ms"], 50.0)
        self.assertIsNone(combined["requests"]["p95_upper_bound_ms"])
        self.assertIsNone(combined["requests"]["p99_upper_bound_ms"])


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
