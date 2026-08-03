import json
import tempfile
import unittest
from pathlib import Path

from bench.analyze import analyze_run


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
            self.assertTrue(summary["comparisons"][0]["workload_equivalent"])
            self.assertEqual(summary["headline"]["status"], "supported")
            self.assertTrue((run_dir / "report.md").exists())
            self.assertTrue((run_dir / "summary.json").exists())
            self.assertTrue(
                (run_dir / "plots" / "gateway-overhead-vs-concurrency.svg").exists()
            )
            report = (run_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("added 3.00 ms p50", report)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
