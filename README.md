# Secure GPU Inference Gateway

An inference gateway demo built around a mock backend. It shows the security control plane a real model-serving system needs: who can call which model, why the call is allowed, how abuse is throttled, and what evidence survives afterward.

The backend is a mock. There is no GPU, no model weights, and no cloud account required. The point is the control plane, not the inference.

## Features

- JWT authentication with issuer, audience, expiry, and role checks.
- Role-based model authorization with reason-for-access enforcement.
- Fixed-window request limits per principal and model.
- Token-budget limits based on estimated input tokens.
- Structured JSONL audit logging with trace context.
- Prometheus `/metrics` for auth, policy, limiter, and latency.
- Sanitized trace export in OpenTelemetry span form.
- OTLP/HTTP collector payload generation.
- Mock GPU backend, with an optional OpenAI-compatible adapter and bounded aggregate probe.
- Unit tests plus a threat model and architecture notes.

## Architecture

The service keeps concerns separate. `gateway/app.py` orchestrates each request. `gateway/identity.py` verifies bearer JWTs. `gateway/policy.py` decides role and reason-for-access. `gateway/rate_limit.py` and `gateway/token_budget.py` enforce request and token limits. `gateway/audit.py` writes evidence. `gateway/metrics.py` exposes Prometheus counters. `gateway/trace_exporter.py` and `gateway/otlp_export.py` produce sanitized spans and collector payloads. The mock backend lives in `gateway/mock_inference.py`.

See `ARCHITECTURE.md` and `THREAT_MODEL.md` for the full picture.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn gateway.app:app --reload
```

Health and metrics:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/metrics
```

Inference request:

```bash
curl -X POST http://localhost:8000/v1/infer/mission-summarizer \
  -H "Content-Type: application/json" \
  -H "X-Principal-Id: analyst-1" \
  -d "{\"input\":\"Summarize synthetic maintenance delays\", \"reason\":\"readiness review\"}"
```

The demo header is enabled by default for convenience. To exercise the JWT path:

```bash
set OIDC_ISSUER=https://issuer.example.com
set OIDC_AUDIENCE=secure-gpu-inference-gateway
set OIDC_JWT_HS256_SECRET=local-review-secret
set ALLOW_DEMO_PRINCIPALS=false
```

Then send `Authorization: Bearer <token>` with `sub`, `iss`, `aud`, `exp`, and a `roles` or `scope` claim matching the model policy. HS256 is for local testing. Production OIDC should use JWKS-backed signing and key rotation.

Local dashboard stack:

```bash
docker compose up --build
```

Prometheus runs at `http://localhost:9090`, Grafana at `http://localhost:3000`. The dashboard is provisioned from `deploy/grafana/dashboards/security-gateway.json`, and the collector receives spans on `http://localhost:4318/v1/traces`.

## Evidence artifacts

The repo commits real output artifacts so a reviewer can inspect them without running anything:

- [Sanitized trace evidence](artifacts/sanitized-trace-evidence.jsonl)
- [OTLP collector payload](artifacts/otlp-collector-payload.json)
- [Workload-readiness evidence](artifacts/workload-readiness-evidence.json)
- [Capacity plan evidence](artifacts/capacity-plan-evidence.json)
- [Distributed-limiter evidence](artifacts/distributed-limiter-evidence.json)
- [Deployment-readiness evidence](artifacts/deployment-readiness-evidence.json)
- [Resilience-drill evidence](artifacts/resilience-drill-evidence.json)
- [Bounded backend-probe evidence](artifacts/backend-probe-evidence.json)
- [Grafana dashboard](deploy/grafana/dashboards/security-gateway.json)

The sanitized trace omits prompt text, model output, access reason, and principal ID. One span from `artifacts/sanitized-trace-evidence.jsonl`:

```json
{
  "http.route": "/v1/infer/{model_id}",
  "ai.gateway.model_id": "mission-summarizer",
  "ai.gateway.outcome": "allowed",
  "ai.gateway.auth_method": "demo-header",
  "ai.gateway.estimated_input_tokens": 10,
  "ai.gateway.token_budget_limit": 8000,
  "ai.gateway.latency_ms": 7.25,
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"
}
```

The checked backend-probe artifact records a four-request local endpoint
sample with response-shape validation, 100% success, and aggregate latency
percentiles. It intentionally excludes request bodies, decoded output, API
keys, endpoint URLs, and principal identities. This is endpoint-readiness
evidence, not a claim about production capacity or a live GPU fleet.

Regenerate any artifact from its module, for example `python -m gateway.workload_replay --output artifacts/workload-readiness-evidence.json`. The capacity, workload, limiter, deployment, and resilience artifacts are synthetic. They exercise the planning and gate logic, not a real fleet.

## Optional model-serving adapter

The mock backend is the default. To route an allowed request to a vLLM- or SGLang-style OpenAI-compatible endpoint, set `INFERENCE_BACKEND_COMPLETIONS_URL` to the backend base URL, its `/v1` URL, or the full `/v1/completions` URL. The adapter sends a bounded non-streaming request, validates the response shape, applies a five-second timeout, and returns a generic `502` with the trace ID on failure. Set `INFERENCE_BACKEND_API_KEY` through a secret when auth is required. The key, prompt, output, and endpoint errors stay out of audit and trace records.

```powershell
$env:INFERENCE_BACKEND_COMPLETIONS_URL = "http://localhost:8001/v1"
$env:INFERENCE_BACKEND_TIMEOUT_MS = "5000"
python -m uvicorn gateway.app:app --port 8000
```

## Test

```bash
python -m unittest discover -s tests
```

## Next steps

- Replace local HS256 tokens with JWKS-backed OIDC key rotation.
- Wire the limiter plan into a live Redis or Envoy integration.
- Replace synthetic capacity and resilience inputs with measured backend telemetry.
- Run the bounded backend probe against a real authorized model-serving endpoint
  and publish only aggregate latency, success, and token totals after review.
- Add policy-as-code examples, redaction controls, and negative authorization tests.
- Add CI supply-chain evidence: SBOM, dependency scanning, container scanning.
