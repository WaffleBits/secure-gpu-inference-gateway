# Secure GPU Inference Gateway

Security-focused AI infrastructure demo for OIDC/JWT-authenticated model access, role-based authorization, per-model request and token-budget limits, trace-aware audit logs, synthetic capacity/cost planning, and policy-driven inference routing.

This repository uses a mock inference backend so the security and infrastructure logic can be reviewed without GPU hardware, model weights, proprietary data, or cloud credentials.

## Why This Exists

AI infrastructure teams need more than a model endpoint. They need controls around who can call which model, why the call is allowed, how abuse is throttled, and what evidence exists after the fact. This project models that control plane in a public-safe way.

This is the flagship portfolio project for the platform-security-to-AI-infrastructure lane. It is meant to demonstrate security software around model-serving systems: identity, authorization, policy decisions, audit evidence, rate limits, observability, deployment posture, and a credible roadmap toward redaction, policy-as-code, supply-chain checks, and compliance evidence automation.

## Features

- FastAPI inference gateway.
- OIDC-style bearer JWT validation with issuer, audience, expiry, and role-claim checks.
- Demo principals and role-based model policies for local review.
- Reason-for-access enforcement for sensitive models.
- Fixed-window request and estimated input-token limiting by principal and model.
- Structured JSONL audit logging with authentication and trace context evidence.
- W3C `traceparent` propagation for OpenTelemetry-compatible request correlation.
- Prometheus-compatible `/metrics` endpoint for authentication, policy, limiter, token-throughput, and latency telemetry.
- Opt-in sanitized trace JSONL export for local OpenTelemetry-shaped span evidence.
- Synthetic capacity and cost-to-serve planning artifact tied to configured model policies.
- Prometheus and Grafana provisioning files for local observability review.
- Mock GPU inference backend with latency metadata.
- Focused unit tests for policy and limiter behavior.
- Threat model and architecture notes.
- Kubernetes deployment example with health probes and scrape annotations.
- SLO, alert, and incident runbook notes.

## Engineering Scope

This repo implements controls around model access, explains why a request was allowed or denied, preserves audit evidence, measures service behavior, and keeps policy, rate limiting, API routing, metrics, and inference concerns separated.

Relevant areas:

- Security infrastructure: OIDC/JWT authentication boundaries, authorization policy, audit trails, rate limits, and threat modeling.
- AI platform engineering: protected inference paths, model routing extension points, mockable backends, and operational metadata.
- Infrastructure/SRE: Prometheus-style metrics, health probes, SLO notes, runbooks, and Kubernetes deployment shape.
- Backend engineering: FastAPI service structure, focused tests, clear module boundaries, and production-control roadmap.

## Reviewer Fast Path

- Start with `gateway/app.py` for request orchestration.
- Review `gateway/identity.py` for bearer JWT verification and demo-principal fallback controls.
- Review `gateway/policy.py` for role and reason-for-access decisions.
- Review `gateway/rate_limit.py` and `gateway/token_budget.py` for request-count and token-budget limiter behavior.
- Review `gateway/metrics.py` and `/metrics` for Prometheus-compatible operational telemetry.
- Review `gateway/audit.py` for structured evidence.
- Review `gateway/trace_exporter.py` for sanitized trace span export without prompt, output, access-reason, or principal identifiers.
- Review `gateway/trace_context.py` for W3C trace context parsing and response propagation.
- Review `gateway/capacity_plan.py` and `artifacts/capacity-plan-evidence.json` for aggregate synthetic capacity and cost-to-serve modeling.
- Review `deploy/grafana/dashboards/security-gateway.json` for dashboard queries over the gateway metrics.
- Review `docs/OPERATIONS.md` and `deploy/kubernetes/gateway.yaml` for SLO/runbook and deployment thinking.
- Check `tests/` for behavior-focused coverage.
- Read `docs/PORTFOLIO_REVIEW.md` for the role-specific review guide.
- Read `ROADMAP.md` for the next build path toward a secure AI / cloud governance platform.

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn gateway.app:app --reload
```

Health check:

```bash
curl http://localhost:8000/health
```

Metrics:

```bash
curl http://localhost:8000/metrics
```

Sanitized trace evidence:

```bash
set TRACE_EXPORT_PATH=artifacts/local-traces.jsonl
uvicorn gateway.app:app --reload
```

Requests still write the normal audit event, but trace export is intentionally narrower. It records service, route, model, outcome, auth method, estimated input-token count, configured token budget, latency, and trace identifiers; it does not record prompt text, model output, access reason, subject, or principal ID. A checked example is in `artifacts/sanitized-trace-evidence.jsonl`.

Capacity plan evidence:

```bash
python -m gateway.capacity_plan --output artifacts/capacity-plan-evidence.json
```

The checked capacity artifact is synthetic aggregate data. It compares configured request and input-token policy limits against modeled per-model request capacity, input-token capacity, decode-token capacity, p95 latency, utilization assumptions, and cost-to-serve estimates. It is meant for review of the planning logic, not as a claim about a production fleet.

Local dashboard stack:

```bash
docker compose up --build
```

Then open Prometheus at `http://localhost:9090` and Grafana at `http://localhost:3000`. The dashboard is provisioned from `deploy/grafana/dashboards/security-gateway.json`.

Inference request:

```bash
curl -X POST http://localhost:8000/v1/infer/mission-summarizer \
  -H "Content-Type: application/json" \
  -H "X-Principal-Id: analyst-1" \
  -d "{\"input\":\"Summarize synthetic maintenance delays\", \"reason\":\"readiness review\"}"
```

The local demo header is enabled by default for reviewer convenience. To exercise the JWT path, set:

```bash
set OIDC_ISSUER=https://issuer.example.com
set OIDC_AUDIENCE=secure-gpu-inference-gateway
set OIDC_JWT_HS256_SECRET=local-review-secret
set ALLOW_DEMO_PRINCIPALS=false
```

Then send an `Authorization: Bearer <token>` header with `sub`, `iss`, `aud`, `exp`, and a `roles` or `scope` claim matching the target model policy. The verifier uses HS256 for public-safe local testing; production OIDC deployments should use JWKS-backed asymmetric signing and key rotation.

## Test

```bash
python -m unittest discover -s tests
```

## Engineering Notes

This project covers:

- Access-control thinking around model-serving systems.
- Public-safe audit and policy design.
- Issuer-bound JWT validation, role claim mapping, and demo-auth disablement.
- W3C trace context propagation for request correlation across a model-serving control plane.
- Prometheus-compatible metrics for authentication outcomes, policy denials, request/token limiting, input-token throughput, and inference latency.
- Sanitized trace export that proves request correlation without leaking prompts, outputs, reasons, or principal identifiers.
- Synthetic capacity and cost-to-serve projection that connects policy budgets to modeled request, token, latency, utilization, and cost assumptions.
- Local Prometheus/Grafana review files for model-access, auth, denial, and latency telemetry.
- Kubernetes-ready health probes, scrape annotations, and non-root runtime posture.
- Backend service design with clear separation between API, policy, rate limiting, and inference.
- A credible path toward production controls such as JWKS key rotation, mTLS, external policy engines, GPU telemetry, and model routing.

## Gaps Worth Closing Next

- Replace local HS256 review tokens with JWKS-backed OIDC key rotation.
- Replace in-memory request and token-budget limiters with Redis-backed or gateway-level distributed controls.
- Upgrade the local trace JSONL proof into full OpenTelemetry SDK export through an OTLP collector, then capture Grafana screenshots from synthetic traffic.
- Replace synthetic capacity inputs with measured backend profiles once a real model-serving adapter exists.
- Add policy-as-code examples, redaction controls, and negative authorization tests.
- Add CI supply-chain evidence such as SBOM generation, dependency scanning, and container scanning.
- Add SOC2/FedRAMP-inspired control mapping notes without claiming certification or production authorization.
- Replace the `emptyDir` demo audit volume with durable log shipping or object storage retention.

## Public-Safe Scope

All users, models, prompts, and outputs are synthetic. Do not add secrets, customer data, real tokens, production logs, model weights, or sensitive operational details.
