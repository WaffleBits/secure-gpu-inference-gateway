# Threat Model

## Assets

- Model access permissions.
- Prompt and response audit trails.
- Model capacity and GPU-backed inference budget.
- Service credentials and deployment configuration.

## Primary Abuse Cases

- Unauthorized user calls a restricted model.
- Authorized user calls a model without a mission or business reason.
- User bypasses rate limits by hammering a high-cost model.
- Prompt content is logged without structure or retention controls.
- Service errors hide failed authorization attempts.

## MVP Controls

- Every request resolves a principal.
- Every model has explicit allowed roles.
- Sensitive models require a non-empty reason.
- Requests are rate-limited by principal and model.
- Allow and deny decisions are written to an audit log.
- The mock backend avoids real model weights or data.

## Residual Risk

This is a demo. Production deployments should add external identity, centralized policy, durable audit storage, secrets management, network controls, dependency scanning, and SLO-driven monitoring.

