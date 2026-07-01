import unittest

from gateway.token_budget import estimate_input_tokens


class TokenBudgetTest(unittest.TestCase):
    def test_estimates_tokens_from_nonempty_text(self) -> None:
        self.assertEqual(estimate_input_tokens("abcd"), 1)
        self.assertEqual(estimate_input_tokens("abcde"), 2)
        self.assertEqual(estimate_input_tokens("  abcdefgh  "), 2)

    def test_empty_text_still_costs_one_token(self) -> None:
        self.assertEqual(estimate_input_tokens("   "), 1)


if __name__ == "__main__":
    unittest.main()
