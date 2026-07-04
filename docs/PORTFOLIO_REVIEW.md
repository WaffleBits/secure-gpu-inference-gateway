# Portfolio Review Notes

This project is intentionally designed as a public-safe AI security infrastructure artifact.

## What To Review

- `gateway/app.py`: request lifecycle, headers, policy calls, rate limiting, and response shape.
- `gateway/identity.py`: issuer-bound JWT verification, role claim mapping, and demo-auth disablement.
- `gateway/trace_context.py`: W3C trace context parsing and child-span propagation.
- `gateway/policy.py`: role-based model authorization and reason-for-access checks.
- `gateway/rate_limit.py`: fixed-window request and token-budget limiter behavior by principal and model.
- `gateway/token_budget.py`: deterministic estimated input-token accounting.
- `gateway/metrics.py`: Prometheus-compatible authentication, policy, limiter, token-throughput, and latency samples.
- `gateway/audit.py`: structured JSONL evidence for allowed and denied requests with auth and trace fields.
- `gateway/trace_exporter.py`: sanitized trace span export and OTLP/HTTP collector payload generation for local observability evidence.
- `gateway/otlp_export.py`: CLI path for generating or sending collector-ready trace payloads.
- `gateway/workload_replay.py`: deterministic aggregate replay for allowed, denied, request-limited, and token-budget-limited paths.
- `gateway/capacity_plan.py`: synthetic capacity and cost-to-serve projection tied to model policies.
- `gateway/mock_inference.py`: reviewable model-serving boundary without private models or GPU hardware.
- `artifacts/workload-readiness-evidence.json`: checked readiness artifact for guardrail coverage, latency gates, and model pressure summaries.
- `artifacts/capacity-plan-evidence.json`: checked aggregate capacity artifact for local review.
- `artifacts/otlp-collector-payload.json`: checked collector-ready trace payload generated from sanitized span evidence.
- `deploy/otel-collector/collector-config.yaml`: local collector intake for OTLP/HTTP trace review.
- `deploy/grafana/dashboards/security-gateway.json`: dashboard queries for request outcomes, latency, auth, denial, and model policy review.
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
- Proving token-budget abuse control without writing prompt text into metrics or trace spans.
- Handling request correlation through OpenTelemetry-compatible W3C trace context.
- Exporting sanitized trace evidence that keeps prompt, output, access-reason, subject, and principal identifiers out of observability artifacts.
- Producing OTLP/HTTP collector-ready trace payloads without widening the public trace data boundary.
- Replaying synthetic workload pressure through policy, request-limit, and token-budget controls without storing request bodies, identities, or outputs.
- Connecting model policy limits to synthetic capacity, utilization, latency, and cost-to-serve estimates without using private workload data.
- Providing Prometheus/Grafana review files that turn gateway metrics into operational panels.
- Designing a mockable inference boundary that can later route to real model-serving backends.
- Showing Kubernetes deployment thinking without requiring cloud credentials.
- Keeping the next-build path focused on platform security and AI infrastructure controls instead of generic dashboard work.
- Keeping public portfolio code free of secrets, credentials, private data, and production logs.

## Technical Scope

- Security infrastructure: JWT authentication, authorization, auditability, request/token limiting, and abuse-control thinking.
- AI platform engineering: protected inference paths, model routing, operational metrics, and service boundaries.
- Infrastructure/SRE: health probes, Prometheus metrics, trace propagation, workload-readiness gates, SLOs, and incident runbooks.
- Backend engineering: FastAPI structure, clear modules, focused tests, and production extension points.

## Gaps Worth Closing Next

- Add JWKS-backed OIDC key rotation examples.
- Add mTLS notes for gateway-to-backend communication.
- Replace in-memory request/token budget controls with distributed limiters.
- Capture collector and Grafana screenshots from synthetic traffic.
- Replace synthetic capacity profiles with measured backend profiles after model-serving integration exists.
- Extend workload-readiness replay with backend error-rate, queue-depth, and resilience probes after model-serving integration exists.
- Add policy-as-code and redaction examples with positive and negative test cases.
- Add CI supply-chain evidence such as SBOM generation, dependency scanning, and container scanning.
- Add SOC2/FedRAMP-inspired control mapping notes without implying certification or production authorization.
- Add durable audit-log shipping and retention policy examples.
