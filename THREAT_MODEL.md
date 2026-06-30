# Threat Model

## Assets

- Model access permissions.
- Prompt and response audit trails.
- Model capacity and GPU-backed inference budget.
- Service credentials and deployment configuration.
- Bearer-token trust boundary, issuer/audience configuration, and role claims.
- Trace identifiers used to correlate audit, metrics, and upstream service logs.

## Primary Abuse Cases

- Unauthorized user calls a restricted model.
- A forged, expired, wrong-audience, or wrong-issuer bearer token is accepted.
- Authorized user calls a model without a mission or business reason.
- User bypasses rate limits by hammering a high-cost model.
- Prompt content is logged without structure or retention controls.
- Service errors hide failed authorization attempts.
- A failed request cannot be correlated across gateway, identity, and backend telemetry.

## MVP Controls

- Every request resolves a principal from a verified bearer JWT or explicitly enabled local demo header.
- Bearer JWTs must pass signature, issuer, audience, expiry, and subject validation.
- Every model has explicit allowed roles.
- Sensitive models require a non-empty reason.
- Requests are rate-limited by principal and model.
- Allow and deny decisions are written to an audit log with authentication method and trace IDs.
- W3C trace context is accepted and propagated so failures can be traced across service boundaries.
- Optional trace export writes sanitized span evidence without prompt text, model outputs, access reason, subject, or principal ID.
- The mock backend avoids real model weights or data.

## Residual Risk

This is a demo. Production deployments should add JWKS-backed OIDC key rotation, centralized policy, durable audit storage, secrets management, network controls, dependency scanning, OpenTelemetry SDK export through an OTLP collector, and SLO-driven monitoring.
