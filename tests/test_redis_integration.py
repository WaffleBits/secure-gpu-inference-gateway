import os
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

from gateway.rate_limit import (
    RedisFixedWindowRateLimiter,
    RedisFixedWindowTokenBudgetLimiter,
)


REDIS_TEST_URL = os.getenv("REDIS_TEST_URL")


@unittest.skipUnless(
    REDIS_TEST_URL, "set REDIS_TEST_URL for live Redis atomicity tests"
)
class RedisAtomicityIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        import redis

        self.redis = redis
        self.prefix = f"sgig-test-{uuid.uuid4().hex}"
        self.clients = [redis.Redis.from_url(REDIS_TEST_URL) for _ in range(4)]
        for client in self.clients:
            client.ping()

    def tearDown(self) -> None:
        keys = list(self.clients[0].scan_iter(match=f"{self.prefix}:*", count=100))
        if keys:
            self.clients[0].delete(*keys)

    def test_request_limit_is_atomic_across_replica_clients(self) -> None:
        limiters = [
            RedisFixedWindowRateLimiter(client, key_prefix=self.prefix)
            for client in self.clients
        ]

        with ThreadPoolExecutor(max_workers=32) as executor:
            decisions = list(
                executor.map(
                    lambda index: limiters[index % len(limiters)].allow(
                        "shared-principal", "benchmark-echo", limit=25
                    ),
                    range(100),
                )
            )

        self.assertEqual(sum(decisions), 25)

    def test_token_budget_is_atomic_and_callers_are_isolated(self) -> None:
        limiters = [
            RedisFixedWindowTokenBudgetLimiter(client, key_prefix=self.prefix)
            for client in self.clients
        ]
        with ThreadPoolExecutor(max_workers=20) as executor:
            decisions = list(
                executor.map(
                    lambda index: limiters[index % len(limiters)].allow(
                        "principal-a", "benchmark-echo", cost=3, limit=30
                    ),
                    range(20),
                )
            )

        self.assertEqual(sum(decisions), 10)
        self.assertTrue(
            limiters[0].allow("principal-b", "benchmark-echo", cost=30, limit=30)
        )

    def test_window_expiration_allows_new_work(self) -> None:
        limiter = RedisFixedWindowRateLimiter(
            self.clients[0],
            window_seconds=1,
            key_prefix=self.prefix,
        )
        self.assertTrue(limiter.allow("principal", "model", limit=1))
        self.assertFalse(limiter.allow("principal", "model", limit=1))
        time.sleep(1.1)
        self.assertTrue(limiter.allow("principal", "model", limit=1))


if __name__ == "__main__":
    unittest.main()
