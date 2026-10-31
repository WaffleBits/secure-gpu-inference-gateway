import argparse
import unittest

from bench.overhead import (
    AUDIENCE,
    ISSUER,
    make_jwt,
    paired_added_latency,
    percentile,
    render_report,
    run_benchmark,
    stream_chunks,
    summarize_run,
)
from gateway.identity import AuthSettings, verify_hs256_jwt


class OverheadBenchmarkTest(unittest.TestCase):
    def test_percentile_uses_nearest_rank(self) -> None:
        values = [float(value) for value in range(1, 101)]

        self.assertEqual(percentile(values, 0.50), 50.0)
        self.assertEqual(percentile(values, 0.99), 99.0)
        self.assertEqual(percentile([7.0], 0.99), 7.0)

    def test_summary_reports_microseconds_and_rate(self) -> None:
        summary = summarize_run([0.001, 0.002], elapsed_s=0.5, failures=0)

        self.assertEqual(summary["requests_per_second"], 4)
        self.assertAlmostEqual(summary["p50_us"], 1000)
        self.assertAlmostEqual(summary["p99_us"], 2000)

    def test_added_latency_pairs_repetitions(self) -> None:
        direct = [
            {"p50_us": 100, "p95_us": 200, "p99_us": 300},
            {"p50_us": 500, "p95_us": 600, "p99_us": 700},
        ]
        gateway = [
            {"p50_us": 150, "p95_us": 260, "p99_us": 370},
            {"p50_us": 530, "p95_us": 640, "p99_us": 750},
        ]

        added = paired_added_latency(direct, gateway)

        self.assertEqual(added["p50_us"], {"median": 40, "min": 30, "max": 50})

    def test_added_latency_rejects_unpaired_runs(self) -> None:
        with self.assertRaises(ValueError):
            paired_added_latency([{"p50_us": 1}], [])

    def test_token_passes_the_gateway_verifier(self) -> None:
        settings = AuthSettings(
            jwt_issuer=ISSUER, jwt_audience=AUDIENCE, jwt_hs256_secret="secret"
        )

        claims = verify_hs256_jwt(make_jwt("secret", 60), settings)

        self.assertEqual(claims["roles"], ["analyst"])

    def test_stream_has_tokens_usage_and_done(self) -> None:
        self.assertEqual(len(stream_chunks(4)), 6)
        self.assertIn(b"[DONE]", stream_chunks(4)[-1])

    def test_small_run_exercises_the_control_path(self) -> None:
        args = argparse.Namespace(
            pin=False,
            redis_url=None,
            prompt_words=4,
            tokens=2,
            requests=20,
            warmup=2,
            repetitions=1,
            concurrency=[2],
        )

        results = run_benchmark(args)

        self.assertEqual(results["control_path_check"]["audit_events"], 22)
        self.assertEqual(results["conditions"][0]["gateway"][0]["failures"], 0)
        self.assertIn("Added p50", render_report(results))


if __name__ == "__main__":
    unittest.main()
