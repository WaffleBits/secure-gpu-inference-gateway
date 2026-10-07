import os
import unittest
from unittest.mock import patch

from gateway.identity import AuthSettings, resolve_principal


class DemoAuthDefaultTest(unittest.TestCase):
    def test_unset_environment_disables_demo_principal_authentication(self) -> None:
        self.assertFalse(AuthSettings().allow_demo_principals)
        with patch.dict(os.environ, {}, clear=True):
            settings = AuthSettings.from_env()

        self.assertFalse(settings.allow_demo_principals)
        resolved = resolve_principal(None, "analyst-1", settings)
        self.assertIsNone(resolved.principal)
        self.assertEqual(resolved.failure_reason, "demo principals disabled")

    def test_demo_principal_authentication_requires_explicit_opt_in(self) -> None:
        with patch.dict(os.environ, {"ALLOW_DEMO_PRINCIPALS": "true"}, clear=True):
            settings = AuthSettings.from_env()

        self.assertTrue(settings.allow_demo_principals)
        resolved = resolve_principal(None, "analyst-1", settings)
        self.assertIsNotNone(resolved.principal)
        self.assertEqual(resolved.auth_method, "demo-header")

    def test_explicit_false_disables_demo_principal_authentication(self) -> None:
        with patch.dict(os.environ, {"ALLOW_DEMO_PRINCIPALS": "false"}, clear=True):
            settings = AuthSettings.from_env()

        self.assertFalse(settings.allow_demo_principals)
        resolved = resolve_principal(None, "analyst-1", settings)
        self.assertIsNone(resolved.principal)
        self.assertEqual(resolved.failure_reason, "demo principals disabled")


if __name__ == "__main__":
    unittest.main()
