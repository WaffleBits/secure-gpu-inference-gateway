import unittest

from gateway.rate_limit import FixedWindowRateLimiter


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


if __name__ == "__main__":
    unittest.main()

