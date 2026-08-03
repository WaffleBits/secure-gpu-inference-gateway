# Secure GPU Inference Gateway

An inference gateway with a deterministic mock backend for review and a real,
streaming OpenAI-compatible vLLM path for measurement. It shows who can call
which model, why the call is allowed, how abuse is throttled, what evidence
survives afterward, and what the complete control path costs.

The default quick start remains GPU-free. The real benchmark is opt-in and never
substitutes synthetic values for measured vLLM/GPU results.

## Features

- JWT authentication with issuer, audience, expiry, and role checks.
- Role-based model authorization with reason-for-access enforcement.
- Fixed-window request limits per principal and model.
- Token-budget limits based on estimated input tokens.
- Optional Redis-backed request and token limits using an atomic Lua script;
  memory-backed limits remain the default for local review.
- Structured JSONL audit logging with trace context.
- Prometheus `/metrics` for auth, policy, limiter, and latency.
- Sanitized trace export in OpenTelemetry span form.
- OTLP/HTTP collector payload generation.
- Mock GPU backend plus pooled non-streaming and streaming OpenAI-compatible adapters.
- A full-policy `/v1/completions` proxy suitable for direct-vLLM versus gateway comparison.
- Reproducible official-vLLM benchmark orchestration with TTFT, TPOT, ITL,
  end-to-end percentiles, raw samples, resource capture, paired analysis, and plots.
- Aggregate telemetry snapshot that correlates gateway counters and latency histograms with probe evidence.
- Unit tests plus a threat model and architecture notes.
- CI supply-chain evidence: pinned dependency audit, SPDX image SBOM, and a
  high-severity container vulnerability gate.
- Deployment posture checks for non-root execution, dropped capabilities,
  read-only root filesystem, health probes, resource limits, and metrics.

## Architecture

The service keeps concerns separate. `gateway/app.py` orchestrates each request. `gateway/identity.py` verifies bearer JWTs. `gateway/policy.py` decides role and reason-for-access. `gateway/rate_limit.py` enforces memory-backed limits by default or Redis-backed atomic limits when explicitly enabled. `gateway/audit.py` writes evidence. `gateway/metrics.py` exposes Prometheus counters. `gateway/trace_exporter.py` and `gateway/otlp_export.py` produce sanitized spans and collector payloads. The mock backend lives in `gateway/mock_inference.py`.

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

Optional Redis limiter mode:

```powershell
pip install -r requirements-redis.txt
$env:RATE_LIMIT_BACKEND = "redis"
$env:REDIS_URL = "redis://localhost:6379/0"
$env:REDIS_KEY_PREFIX = "sgig"
uvicorn gateway.app:app
```

Redis mode hashes principal identifiers before constructing keys and evaluates
one atomic fixed-window Lua script per request-count or input-token decision.
The application fails closed at startup if the explicitly selected Redis
backend is unavailable. The checked-in demo and tests remain dependency-free
and use the memory backend unless `RATE_LIMIT_BACKEND=redis` is set.

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
- [Telemetry-correlation evidence](artifacts/telemetry-correlation-evidence.json)
- [Grafana dashboard](deploy/grafana/dashboards/security-gateway.json)

The CI workflow also publishes a dependency-audit report and SPDX SBOM for
review. The container scan fails on unresolved high or critical vulnerabilities;
the checked-in posture tests keep the Docker and Kubernetes hardening controls
from silently drifting.

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

The telemetry-correlation artifact turns a safe `/metrics` scrape into an
aggregate review snapshot: request outcomes, estimated input-token totals,
histogram-based latency upper bounds, and the bounded probe result are shown
together. The checked fixture is local review evidence; it does not claim live
fleet capacity, GPU utilization, or customer traffic.

Regenerate any artifact from its module, for example `python -m gateway.workload_replay --output artifacts/workload-readiness-evidence.json`. The capacity, workload, limiter, deployment, and resilience artifacts are synthetic. They exercise the planning and gate logic, not a real fleet.

## Optional model-serving adapter

The mock backend is the default for `POST /v1/infer/{model_id}`. To route an
allowed request to a vLLM- or SGLang-style OpenAI-compatible endpoint, set
`INFERENCE_BACKEND_COMPLETIONS_URL` to the backend base URL, its `/v1` URL, or
the full `/v1/completions` URL. The bounded inference route validates a
non-streaming response; `POST /v1/completions` forwards streaming SSE bytes
without decoding model output. Both return a generic `502` with the trace ID on
failure. Set `INFERENCE_BACKEND_API_KEY` through a secret when auth is required.
The key, prompt, output, endpoint URL, and upstream error stay out of audit and
trace records.

```powershell
$env:INFERENCE_BACKEND_COMPLETIONS_URL = "http://localhost:8001/v1"
$env:INFERENCE_BACKEND_TIMEOUT_MS = "5000"
python -m uvicorn gateway.app:app --port 8000
```

The same configuration enables `POST /v1/completions`. That route accepts a
bounded OpenAI-compatible completion body, applies the normal authentication,
authorization, request limit, input-token budget, audit, metric, and trace path,
then passes streaming SSE bytes through without decoding model output in the
gateway. Backend connections use one process-wide pool. A missing or failing
backend returns a generic `502` without exposing the upstream URL or error.

## Real vLLM benchmark

See [`bench/README.md`](bench/README.md) for the end-to-end benchmark. It uses
the upstream `vllm bench serve` client to compare the same seeded token workloads
directly against vLLM and through the full gateway at concurrency 1 through 64.
Every run saves environment metadata, raw request samples, condition aggregates,
resource samples, Redis limiter latency, a Markdown report, and engineering SVG
plots. The checked RTX 5070 Ti / Qwen2.5-3B run completed 3,000 measured requests
with zero failures. At the predeclared medium workload and concurrency 8, the
full gateway observed 103.83% of direct-vLLM request throughput across 60
requests per path (101.37% to 108.94% across paired repetitions). This is an
observed no-throughput-loss result, not a speedup claim; serving-runtime drift
prevented a defensible positive latency-overhead estimate. See the
[`measured report`](bench/results/20260803T212059Z-rtx5070ti-qwen25-3b-vllm026/report.md)
and [`validation note`](bench/results/20260803T212059Z-rtx5070ti-qwen25-3b-vllm026/VALIDATION.md).

## Test

```bash
python -m unittest discover -s tests
```

## Next steps

- Replace local HS256 tokens with JWKS-backed OIDC key rotation.
- Run the live Redis atomicity and two-replica correctness profiles on every
  supported deployment target and keep Envoy descriptor parity checked.
- Replace synthetic capacity and resilience inputs with measured backend telemetry.
- Repeat the real vLLM matrix on additional authorized GPU/model configurations
  before drawing hardware-general conclusions.
- Add policy-as-code examples, redaction controls, and negative authorization tests.
- Add CI supply-chain evidence: SBOM, dependency scanning, container scanning.
- Capture a telemetry-correlation snapshot after each explicitly authorized
  endpoint probe, then review it before replacing synthetic capacity inputs.
