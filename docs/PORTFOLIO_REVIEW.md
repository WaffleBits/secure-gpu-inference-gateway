# Portfolio Review Notes

This project is intentionally designed as a public-safe AI security infrastructure artifact.

## What To Review

- `gateway/app.py`: request lifecycle, headers, policy calls, rate limiting, and response shape.
- `gateway/policy.py`: role-based model authorization and reason-for-access checks.
- `gateway/rate_limit.py`: fixed-window limiter behavior by principal and model.
- `gateway/metrics.py`: Prometheus-compatible counters and latency histogram samples.
- `gateway/audit.py`: structured JSONL evidence for allowed and denied requests.
- `gateway/mock_inference.py`: reviewable model-serving boundary without private models or GPU hardware.
- `docs/OPERATIONS.md`: SLOs, alert candidates, and incident runbooks.
- `deploy/kubernetes/gateway.yaml`: health probes, non-root container posture, and scrape annotations.
- `tests/`: behavior-focused coverage for security and limiter decisions.

## What This Demonstrates

- Separating security policy from API orchestration.
- Making model access decisions explainable and testable.
- Preserving audit evidence without exposing sensitive prompts, users, or model outputs.
- Exposing model-serving control-plane metrics that support SRE review.
- Designing a mockable inference boundary that can later route to real model-serving backends.
- Showing Kubernetes deployment thinking without requiring cloud credentials.
- Keeping public portfolio code free of secrets, credentials, private data, and production logs.

## Technical Scope

- Security infrastructure: authorization, auditability, rate limiting, and abuse-control thinking.
- AI platform engineering: protected inference paths, model routing, operational metrics, and service boundaries.
- Infrastructure/SRE: health probes, Prometheus metrics, SLOs, and incident runbooks.
- Backend engineering: FastAPI structure, clear modules, focused tests, and production extension points.

## Gaps Worth Closing Next

- Add OIDC/JWT verification and key rotation examples.
- Add mTLS notes for gateway-to-backend communication.
- Add OpenTelemetry traces and dashboard screenshots.
- Add policy-as-code examples with positive and negative test cases.
- Add durable audit-log shipping and retention policy examples.
