import threading
import unittest
from unittest.mock import patch

from bench.redis_replica_check import run_check


class RedisReplicaCheckTest(unittest.TestCase):
    def test_requires_one_shared_limit_and_checks_isolation_and_expiration(
        self,
    ) -> None:
        lock = threading.Lock()
        analyst_count = 0

        def request_fn(url: str, principal: str, index: int) -> int:
            nonlocal analyst_count
            if principal == "security-1" or index == 32:
                return 200
            with lock:
                analyst_count += 1
                return 200 if analyst_count <= 10 else 429

        with patch("bench.redis_replica_check.time.sleep"):
            report = run_check(
                ["http://a", "http://b"],
                request_limit=10,
                window_seconds=2,
                total_requests=30,
                request_fn=request_fn,
            )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["allowed"], 10)
        self.assertEqual(report["limited"], 20)
        self.assertTrue(report["checks"]["caller_isolation"])
        self.assertTrue(report["checks"]["window_expiration"])


if __name__ == "__main__":
    unittest.main()
