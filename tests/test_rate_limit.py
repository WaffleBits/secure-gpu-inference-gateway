import unittest

from gateway.rate_limit import FixedWindowRateLimiter, FixedWindowTokenBudgetLimiter


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


if __name__ == "__main__":
    unittest.main()
