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
- Mock GPU inference backend with latency metadata.
- Focused unit tests for policy and limiter behavior.
- Threat model and architecture notes.

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

## Portfolio Signal

This project is aimed at security infrastructure and AI infrastructure roles. It demonstrates:

- Access-control thinking around model-serving systems.
- Public-safe audit and policy design.
- Backend service design with clear separation between API, policy, rate limiting, and inference.
- A credible path toward production controls such as OIDC, mTLS, external policy engines, GPU telemetry, and model routing.

## Public-Safe Scope

All users, models, prompts, and outputs are synthetic. Do not add secrets, customer data, real tokens, production logs, model weights, or sensitive operational details.

