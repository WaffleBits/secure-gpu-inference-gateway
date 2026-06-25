# Portfolio Review Notes

This project is intentionally designed as a public-safe AI security infrastructure artifact.

## What To Review

- `gateway/app.py`: request lifecycle, headers, policy calls, rate limiting, and response shape.
- `gateway/identity.py`: issuer-bound JWT verification, role claim mapping, and demo-auth disablement.
- `gateway/trace_context.py`: W3C trace context parsing and child-span propagation.
- `gateway/policy.py`: role-based model authorization and reason-for-access checks.
- `gateway/rate_limit.py`: fixed-window limiter behavior by principal and model.
- `gateway/metrics.py`: Prometheus-compatible authentication, policy, limiter, and latency samples.
- `gateway/audit.py`: structured JSONL evidence for allowed and denied requests with auth and trace fields.
- `gateway/mock_inference.py`: reviewable model-serving boundary without private models or GPU hardware.
- `docs/OPERATIONS.md`: SLOs, alert candidates, and incident runbooks.
- `ROADMAP.md`: secure AI / cloud governance roadmap for policy-as-code, redaction, supply-chain evidence, telemetry, and control mapping.
- `deploy/kubernetes/gateway.yaml`: health probes, non-root container posture, and scrape annotations.
- `tests/`: behavior-focused coverage for security and limiter decisions.

## What This Demonstrates

- Separating security policy from API orchestration.
- Validating bearer-token issuer, audience, expiry, signature, subject, and role claims.
- Making model access decisions explainable and testable.
- Preserving audit evidence and trace IDs without exposing sensitive prompts, users, or model outputs.
- Exposing model-serving control-plane metrics that support SRE review.
- Handling request correlation through OpenTelemetry-compatible W3C trace context.
- Designing a mockable inference boundary that can later route to real model-serving backends.
- Showing Kubernetes deployment thinking without requiring cloud credentials.
- Keeping the next-build path focused on platform security and AI infrastructure controls instead of generic dashboard work.
- Keeping public portfolio code free of secrets, credentials, private data, and production logs.

## Technical Scope

- Security infrastructure: JWT authentication, authorization, auditability, rate limiting, and abuse-control thinking.
- AI platform engineering: protected inference paths, model routing, operational metrics, and service boundaries.
- Infrastructure/SRE: health probes, Prometheus metrics, trace propagation, SLOs, and incident runbooks.
- Backend engineering: FastAPI structure, clear modules, focused tests, and production extension points.

## Gaps Worth Closing Next

- Add JWKS-backed OIDC key rotation examples.
- Add mTLS notes for gateway-to-backend communication.
- Add full OpenTelemetry SDK export and dashboard screenshots.
- Add policy-as-code and redaction examples with positive and negative test cases.
- Add CI supply-chain evidence such as SBOM generation, dependency scanning, and container scanning.
- Add SOC2/FedRAMP-inspired control mapping notes without implying certification or production authorization.
- Add durable audit-log shipping and retention policy examples.
