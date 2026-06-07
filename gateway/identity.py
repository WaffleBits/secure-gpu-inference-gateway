from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from gateway.models import Principal
from gateway.registry import PRINCIPALS


class AuthenticationError(ValueError):
    pass


@dataclass(frozen=True)
class AuthSettings:
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_hs256_secret: str | None = None
    roles_claim: str = "roles"
    scopes_claim: str = "scope"
    allow_demo_principals: bool = True
    clock_skew_seconds: int = 60

    @classmethod
    def from_env(cls) -> AuthSettings:
        return cls(
            jwt_issuer=_empty_to_none(os.getenv("OIDC_ISSUER")),
            jwt_audience=_empty_to_none(os.getenv("OIDC_AUDIENCE")),
            jwt_hs256_secret=_empty_to_none(os.getenv("OIDC_JWT_HS256_SECRET")),
            roles_claim=os.getenv("OIDC_ROLES_CLAIM", "roles"),
            scopes_claim=os.getenv("OIDC_SCOPES_CLAIM", "scope"),
            allow_demo_principals=_parse_bool(os.getenv("ALLOW_DEMO_PRINCIPALS"), True),
            clock_skew_seconds=int(os.getenv("OIDC_CLOCK_SKEW_SECONDS", "60")),
        )


@dataclass(frozen=True)
class ResolvedPrincipal:
    principal: Principal | None
    auth_method: str
    issuer: str | None = None
    subject: str | None = None
    failure_reason: str | None = None


def resolve_principal(
    authorization_header: str | None,
    demo_principal_id: str | None,
    settings: AuthSettings,
    now: float | None = None,
) -> ResolvedPrincipal:
    if authorization_header:
        try:
            claims = verify_hs256_jwt(
                extract_bearer_token(authorization_header),
                settings,
                now=now,
            )
            return principal_from_claims(claims, settings)
        except AuthenticationError as exc:
            return ResolvedPrincipal(
                principal=None,
                auth_method="jwt",
                failure_reason=str(exc),
            )

    if demo_principal_id:
        if not settings.allow_demo_principals:
            return ResolvedPrincipal(
                principal=None,
                auth_method="demo-header",
                subject=demo_principal_id,
                failure_reason="demo principals disabled",
            )

        principal = PRINCIPALS.get(demo_principal_id)
        return ResolvedPrincipal(
            principal=principal,
            auth_method="demo-header",
            issuer="local-demo",
            subject=demo_principal_id,
            failure_reason=None if principal else "unknown demo principal",
        )

    return ResolvedPrincipal(
        principal=None,
        auth_method="none",
        failure_reason="missing bearer token or demo principal header",
    )


def extract_bearer_token(authorization_header: str) -> str:
    scheme, _, token = authorization_header.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("invalid authorization scheme")
    return token.strip()


def verify_hs256_jwt(
    token: str,
    settings: AuthSettings,
    now: float | None = None,
) -> Mapping[str, object]:
    if not settings.jwt_hs256_secret:
        raise AuthenticationError("jwt verifier not configured")

    parts = token.split(".")
    if len(parts) != 3:
        raise AuthenticationError("malformed jwt")

    header = _decode_json(parts[0])
    payload = _decode_json(parts[1])

    if header.get("alg") != "HS256":
        raise AuthenticationError("unsupported jwt algorithm")

    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    expected = hmac.new(
        settings.jwt_hs256_secret.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    actual = _base64url_decode(parts[2])
    if not hmac.compare_digest(expected, actual):
        raise AuthenticationError("invalid jwt signature")

    _validate_claims(payload, settings, now=time.time() if now is None else now)
    return payload


def principal_from_claims(
    claims: Mapping[str, object],
    settings: AuthSettings,
) -> ResolvedPrincipal:
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError("missing jwt subject")

    roles = frozenset(
        [
            *_claim_values(claims.get(settings.roles_claim)),
            *_claim_values(claims.get(settings.scopes_claim)),
        ]
    )
    display_name = _first_string(
        claims.get("name"),
        claims.get("preferred_username"),
        claims.get("email"),
        subject,
    )

    return ResolvedPrincipal(
        principal=Principal(
            principal_id=subject,
            display_name=display_name,
            roles=roles,
        ),
        auth_method="jwt",
        issuer=_first_string(claims.get("iss")),
        subject=subject,
    )


def _validate_claims(
    claims: Mapping[str, object],
    settings: AuthSettings,
    now: float,
) -> None:
    if settings.jwt_issuer and claims.get("iss") != settings.jwt_issuer:
        raise AuthenticationError("unexpected issuer")

    if settings.jwt_audience:
        audiences = _claim_values(claims.get("aud"))
        if settings.jwt_audience not in audiences:
            raise AuthenticationError("unexpected audience")

    skew = settings.clock_skew_seconds
    exp = claims.get("exp")
    if exp is not None and _numeric_date(exp, "exp") + skew < now:
        raise AuthenticationError("token expired")

    nbf = claims.get("nbf")
    if nbf is not None and _numeric_date(nbf, "nbf") - skew > now:
        raise AuthenticationError("token not yet valid")

    iat = claims.get("iat")
    if iat is not None and _numeric_date(iat, "iat") - skew > now:
        raise AuthenticationError("token issued in the future")


def _decode_json(segment: str) -> Mapping[str, object]:
    try:
        decoded = _base64url_decode(segment).decode("utf-8")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AuthenticationError("malformed jwt") from exc

    if not isinstance(value, dict):
        raise AuthenticationError("malformed jwt")
    return value


def _base64url_decode(value: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise AuthenticationError("malformed jwt") from exc


def _claim_values(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(part for part in value.replace(",", " ").split() if part)

    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        return frozenset(str(part) for part in value if isinstance(part, str) and part)

    return frozenset()


def _numeric_date(value: object, claim_name: str) -> float:
    if not isinstance(value, int | float):
        raise AuthenticationError(f"invalid {claim_name} claim")
    return float(value)


def _first_string(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _empty_to_none(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()
