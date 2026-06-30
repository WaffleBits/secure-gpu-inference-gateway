# Secure AI Platform Roadmap

This roadmap keeps the project pointed at platform security, security software engineering, and AI infrastructure security. It is intentionally public-safe: no production endpoints, customer prompts, credentials, model weights, classified data, or real operational logs.

## Positioning

The project should read as a secure AI / cloud governance platform, not as a generic FastAPI demo. The core story is:

- LLM gateway for controlled model access.
- Identity-aware authorization and reason-for-access checks.
- Policy decisions that are explainable, testable, and auditable.
- Prompt/output handling controls such as secrets and PII redaction.
- Kubernetes-shaped deployment and operational telemetry.
- Evidence automation that can map technical controls to security review workflows.

## Milestone 1: Trust Boundary Hardening

Status: partially implemented.

- Keep OIDC/JWT validation issuer-bound, audience-bound, expiry-aware, and testable.
- Add JWKS-backed key rotation example for asymmetric token validation.
- Add mTLS notes for gateway-to-model-backend calls.
- Replace local demo principals with a disabled-by-default review mode.
- Add negative authorization tests for missing roles, wrong audience, expired tokens, and missing reason-for-access.

## Milestone 2: Policy-As-Code

Status: next.

- Move model policies into signed config, OPA, Cedar, or another external policy engine.
- Add policy fixtures that cover allow, deny, and reason-required outcomes.
- Add change-review notes that explain how policy changes would be reviewed before deployment.
- Emit policy decision IDs in audit logs without leaking sensitive prompt data.

## Milestone 3: Redaction And Data Handling

Status: next.

- Add request and response redaction hooks for secrets, credentials, and synthetic PII.
- Add tests proving redaction happens before audit persistence.
- Add per-model data-handling labels such as public, internal, sensitive, and restricted.
- Keep prompt and output samples synthetic and intentionally boring.

## Milestone 4: Supply Chain And Deployment Evidence

Status: planned.

- Generate an SBOM in CI.
- Add dependency and container image scanning.
- Add Kubernetes admission-control notes for non-root containers, read-only filesystem posture, and resource limits.
- Add Terraform or IaC examples only after the runtime controls are credible.
- Document which checks are real CI gates versus roadmap items.

## Milestone 5: Observability And Incident Response

Status: partially implemented.

- Keep the Prometheus `/metrics` endpoint and provisioned Grafana dashboard reviewable under local `docker compose`.
- Keep sanitized trace JSONL export opt-in and free of prompt text, model outputs, access reason, subjects, and principal IDs.
- Upgrade the trace JSONL proof to full OpenTelemetry SDK export through an OTLP collector.
- Add Grafana dashboard screenshots using synthetic traffic.
- Add SLO burn-rate alert examples for auth failures, policy denials, rate limiting, and inference latency.
- Add incident exercises for token abuse, model access denial spikes, audit sink failure, and backend saturation.

## Milestone 6: Model-Serving Integration

Status: planned.

- Add adapters for Triton-compatible, vLLM-compatible, or SGLang-compatible backends.
- Keep mock backend tests so the repo remains reviewable without GPU hardware.
- Add GPU/DCGM telemetry correlation when real backend integration exists.
- Track token throughput, queue depth, error rate, and per-model latency without exposing prompt contents.

## Milestone 7: Control Mapping

Status: planned.

- Add a lightweight control map that links implemented controls to security-review evidence.
- Use SOC2/FedRAMP-inspired language only as a mapping aid; do not claim certification, authorization, or production compliance.
- Show evidence artifacts such as audit events, policy fixtures, tests, CI scan outputs, runbooks, and dashboards.

## Reviewer Principle

Every roadmap item should either strengthen the model-serving control plane or create evidence a platform/security team would expect to review. If a feature does not improve authorization, policy, auditability, observability, deployment safety, or incident response, it does not belong in this repo.
