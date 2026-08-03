import unittest
from unittest.mock import patch

from gateway.rate_limit import (
    FixedWindowRateLimiter,
    FixedWindowTokenBudgetLimiter,
    REDIS_FIXED_WINDOW_LUA,
    RedisFixedWindowRateLimiter,
    RedisFixedWindowTokenBudgetLimiter,
    build_limiters_from_env,
)


class FakeRedis:
    def __init__(self, decisions: list[list[int]]) -> None:
        self.decisions = decisions
        self.calls: list[tuple[str, int, tuple[str, ...]]] = []

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> list[int]:
        self.calls.append((script, numkeys, keys_and_args))
        return self.decisions.pop(0)


class RateLimitTest(unittest.TestCase):
    def test_blocks_after_limit(self) -> None:
        limiter = FixedWindowRateLimiter(window_seconds=60)

        self.assertTrue(limiter.allow("user-1", "model-1", limit=2))
        self.assertTrue(limiter.allow("user-1", "model-1", limit=2))
        self.assertFalse(limiter.allow("user-1", "model-1", limit=2))

    def test_limits_are_per_principal_and_model(self) -> None:
        limiter = FixedWindowRateLimiter(window_seconds=60)

        self.assertTrue(limiter.allow("user-1", "model-1", limit=1))
        self.assertFalse(limiter.allow("user-1", "model-1", limit=1))
        self.assertTrue(limiter.allow("user-1", "model-2", limit=1))
        self.assertTrue(limiter.allow("user-2", "model-1", limit=1))

    def test_token_budget_blocks_when_window_budget_is_exceeded(self) -> None:
        limiter = FixedWindowTokenBudgetLimiter(window_seconds=60)

        self.assertTrue(limiter.allow("user-1", "model-1", cost=3, limit=5))
        self.assertFalse(limiter.allow("user-1", "model-1", cost=3, limit=5))
        self.assertTrue(limiter.allow("user-1", "model-2", cost=3, limit=5))
        self.assertTrue(limiter.allow("user-2", "model-1", cost=3, limit=5))

    def test_token_budget_rejects_oversized_single_request(self) -> None:
        limiter = FixedWindowTokenBudgetLimiter(window_seconds=60)

        self.assertFalse(limiter.allow("user-1", "model-1", cost=6, limit=5))

    def test_redis_request_limiter_uses_atomic_script_and_hashed_principal(self) -> None:
        client = FakeRedis([[1, 1, 2], [0, 3, 2]])
        limiter = RedisFixedWindowRateLimiter(client, window_seconds=60)

        self.assertTrue(limiter.allow("user-1", "model-1", limit=2))
        self.assertFalse(limiter.allow("user-1", "model-1", limit=2))

        script, numkeys, args = client.calls[0]
        self.assertEqual(script, REDIS_FIXED_WINDOW_LUA)
        self.assertEqual(numkeys, 1)
        self.assertNotIn("user-1", args[0])
        self.assertEqual(args[1:], ("1", "60000", "2"))

    def test_redis_token_limiter_passes_token_cost(self) -> None:
        client = FakeRedis([[1, 3, 5]])
        limiter = RedisFixedWindowTokenBudgetLimiter(client, window_seconds=60)

        self.assertTrue(limiter.allow("user-1", "model-1", cost=3, limit=5))
        self.assertEqual(client.calls[0][2][1:], ("3", "60000", "5"))

    def test_memory_backend_honors_configured_window(self) -> None:
        with patch.dict(
            "os.environ",
            {"RATE_LIMIT_BACKEND": "memory", "RATE_LIMIT_WINDOW_SECONDS": "7"},
            clear=True,
        ):
            request_limiter, token_limiter = build_limiters_from_env()

        self.assertEqual(request_limiter.window_seconds, 7)
        self.assertEqual(token_limiter.window_seconds, 7)


if __name__ == "__main__":
    unittest.main()
