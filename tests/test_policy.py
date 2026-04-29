import unittest

from gateway.policy import evaluate_policy
from gateway.registry import MODEL_POLICIES, PRINCIPALS


class PolicyTest(unittest.TestCase):
    def test_allows_authorized_user_with_reason(self) -> None:
        decision = evaluate_policy(
            PRINCIPALS["security-1"],
            MODEL_POLICIES["threat-triage"],
            "incident triage",
        )

        self.assertTrue(decision.allowed)

    def test_denies_unknown_principal(self) -> None:
        decision = evaluate_policy(None, MODEL_POLICIES["mission-summarizer"], "review")

        self.assertFalse(decision.allowed)
        self.assertIn("unknown principal", decision.reasons)

    def test_denies_role_mismatch(self) -> None:
        decision = evaluate_policy(
            PRINCIPALS["analyst-1"],
            MODEL_POLICIES["threat-triage"],
            "curiosity",
        )

        self.assertFalse(decision.allowed)
        self.assertIn("principal lacks an allowed role for this model", decision.reasons)

    def test_requires_reason_for_sensitive_model(self) -> None:
        decision = evaluate_policy(
            PRINCIPALS["analyst-1"],
            MODEL_POLICIES["mission-summarizer"],
            "",
        )

        self.assertFalse(decision.allowed)
        self.assertIn("reason is required for this model", decision.reasons)


if __name__ == "__main__":
    unittest.main()

