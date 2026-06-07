import base64
import hashlib
import hmac
import json
import time
import unittest

from gateway.identity import AuthSettings, resolve_principal


class IdentityTest(unittest.TestCase):
    def test_resolves_valid_jwt_claims(self) -> None:
        settings = AuthSettings(
            jwt_issuer="https://issuer.example.com",
            jwt_audience="secure-gpu-inference-gateway",
            jwt_hs256_secret="test-secret",
        )
        token = make_token(
            {
                "iss": "https://issuer.example.com",
                "aud": "secure-gpu-inference-gateway",
                "sub": "user-123",
                "name": "Platform Engineer",
                "roles": ["security-engineer"],
                "scope": "mission-user",
                "exp": int(time.time()) + 300,
            },
            "test-secret",
        )

        resolved = resolve_principal(f"Bearer {token}", None, settings)

        self.assertIsNotNone(resolved.principal)
        assert resolved.principal is not None
        self.assertEqual(resolved.auth_method, "jwt")
        self.assertEqual(resolved.subject, "user-123")
        self.assertIn("security-engineer", resolved.principal.roles)
        self.assertIn("mission-user", resolved.principal.roles)

    def test_rejects_unexpected_audience(self) -> None:
        settings = AuthSettings(
            jwt_issuer="https://issuer.example.com",
            jwt_audience="secure-gpu-inference-gateway",
            jwt_hs256_secret="test-secret",
        )
        token = make_token(
            {
                "iss": "https://issuer.example.com",
                "aud": "other-service",
                "sub": "user-123",
                "roles": ["security-engineer"],
                "exp": int(time.time()) + 300,
            },
            "test-secret",
        )

        resolved = resolve_principal(f"Bearer {token}", None, settings)

        self.assertIsNone(resolved.principal)
        self.assertEqual(resolved.failure_reason, "unexpected audience")

    def test_rejects_expired_token(self) -> None:
        settings = AuthSettings(
            jwt_issuer="https://issuer.example.com",
            jwt_audience="secure-gpu-inference-gateway",
            jwt_hs256_secret="test-secret",
            clock_skew_seconds=0,
        )
        token = make_token(
            {
                "iss": "https://issuer.example.com",
                "aud": "secure-gpu-inference-gateway",
                "sub": "user-123",
                "roles": ["security-engineer"],
                "exp": int(time.time()) - 1,
            },
            "test-secret",
        )

        resolved = resolve_principal(f"Bearer {token}", None, settings)

        self.assertIsNone(resolved.principal)
        self.assertEqual(resolved.failure_reason, "token expired")

    def test_can_disable_demo_principal_header(self) -> None:
        resolved = resolve_principal(
            None,
            "security-1",
            AuthSettings(allow_demo_principals=False),
        )

        self.assertIsNone(resolved.principal)
        self.assertEqual(resolved.failure_reason, "demo principals disabled")


def make_token(payload: dict[str, object], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join([encode_json(header), encode_json(payload)])
    signature = hmac.new(
        secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{encode_bytes(signature)}"


def encode_json(value: dict[str, object]) -> str:
    return encode_bytes(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def encode_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


if __name__ == "__main__":
    unittest.main()
