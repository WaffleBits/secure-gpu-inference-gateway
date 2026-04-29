# Secure GPU Inference Gateway

Security-focused AI infrastructure demo for authenticated model access, role-based authorization, per-model rate limits, audit logs, and policy-driven inference routing.

This repository uses a mock inference backend so the security and infrastructure logic can be reviewed without GPU hardware, model weights, proprietary data, or cloud credentials.

## Why This Exists

AI infrastructure teams need more than a model endpoint. They need controls around who can call which model, why the call is allowed, how abuse is throttled, and what evidence exists after the fact. This project models that control plane in a public-safe way.

## Features

- FastAPI inference gateway.
- Demo principals and role-based model policies.
- Reason-for-access enforcement for sensitive models.
- Fixed-window rate limiting by principal and model.
- Structured JSONL audit logging.
- Prometheus-compatible `/metrics` endpoint for policy, limiter, and latency telemetry.
- Mock GPU inference backend with latency metadata.
- Focused unit tests for policy and limiter behavior.
- Threat model and architecture notes.
- Kubernetes deployment example with health probes and scrape annotations.
- SLO, alert, and incident runbook notes.

## Engineering Scope

This repo implements controls around model access, explains why a request was allowed or denied, preserves audit evidence, measures service behavior, and keeps policy, rate limiting, API routing, metrics, and inference concerns separated.

Relevant areas:

- Security infrastructure: authentication boundaries, authorization policy, audit trails, rate limits, and threat modeling.
- AI platform engineering: protected inference paths, model routing extension points, mockable backends, and operational metadata.
- Infrastructure/SRE: Prometheus-style metrics, health probes, SLO notes, runbooks, and Kubernetes deployment shape.
- Backend engineering: FastAPI service structure, focused tests, clear module boundaries, and production-control roadmap.

## Reviewer Fast Path

- Start with `gateway/app.py` for request orchestration.
- Review `gateway/policy.py` for role and reason-for-access decisions.
- Review `gateway/rate_limit.py` for limiter behavior.
- Review `gateway/metrics.py` and `/metrics` for Prometheus-compatible operational telemetry.
- Review `gateway/audit.py` for structured evidence.
- Review `docs/OPERATIONS.md` and `deploy/kubernetes/gateway.yaml` for SLO/runbook and deployment thinking.
- Check `tests/` for behavior-focused coverage.
- Read `docs/PORTFOLIO_REVIEW.md` for the role-specific review guide.

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

Inference request:

```bash
curl -X POST http://localhost:8000/v1/infer/mission-summarizer \
  -H "Content-Type: application/json" \
  -H "X-Principal-Id: analyst-1" \
  -d "{\"input\":\"Summarize synthetic maintenance delays\", \"reason\":\"readiness review\"}"
```

## Test

```bash
python -m unittest discover -s tests
```

## Engineering Notes

This project covers:

- Access-control thinking around model-serving systems.
- Public-safe audit and policy design.
- Prometheus-compatible metrics for policy denials, rate limits, and inference latency.
- Kubernetes-ready health probes, scrape annotations, and non-root runtime posture.
- Backend service design with clear separation between API, policy, rate limiting, and inference.
- A credible path toward production controls such as OIDC, mTLS, external policy engines, GPU telemetry, and model routing.

## Gaps Worth Closing Next

- Replace demo principals with OIDC/JWT verification.
- Add Redis-backed or gateway-level distributed rate limiting.
- Add OpenTelemetry traces and Grafana dashboard screenshots.
- Add policy-as-code examples and negative authorization tests.
- Replace the `emptyDir` demo audit volume with durable log shipping or object storage retention.

## Public-Safe Scope

All users, models, prompts, and outputs are synthetic. Do not add secrets, customer data, real tokens, production logs, model weights, or sensitive operational details.
