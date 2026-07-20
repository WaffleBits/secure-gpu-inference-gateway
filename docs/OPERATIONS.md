# Operations Notes

This service is intentionally small, but it is shaped like an AI infrastructure control plane that can be operated under real reliability and security expectations.

## Service Level Objectives

- Availability: 99.9% successful gateway responses for authorized traffic over 30 days.
- Latency: p95 successful inference gateway latency below 500 ms for the mock backend.
- Policy correctness: 100% of denied requests include a structured decision reason in audit logs and metrics.
- Audit durability: every allowed, policy-denied, and rate-limited inference request writes one audit event.
- Traceability: every inference response and audit event carries a trace ID for request correlation.
- Trace privacy: optional trace exports omit prompt text, generated output, access reason, subject, and principal ID.
- Collector export: sanitized spans can be converted to OTLP/HTTP payloads and sent to a collector without widening the trace data boundary.
- Budget visibility: request-count and estimated input-token budget decisions are visible in audit events and metrics.
- Distributed limiter review: every model policy has request-count and estimated input-token rules mapped to a Redis/Envoy-style external limiter plan before replacing local fixed-window controls.
- Capacity review: model policy changes compare request and input-token limits against a synthetic capacity/cost plan before rollout.
- Workload readiness: synthetic replay covers allowed, policy-denied, rate-limited, and token-budget-limited paths before policy changes are treated as locally reviewable.
- Deployment readiness: local release reviews compose capacity, workload, and limiter evidence into shadow, canary, staged rollout, and rollback gates before serving-path changes are treated as locally reviewable.
- Resilience review: synthetic degradation drills cover latency spikes, backend error bursts, queue saturation, and audit backpressure before serving-path changes are treated as locally reviewable.
- Backend probe review: a bounded endpoint sample records aggregate success, latency, and reported token totals before measured backend capacity or resilience claims are made.
- Telemetry-correlation review: a safe metrics scrape joins gateway outcome/token counters and histogram latency upper bounds with the bounded probe result before measured backend claims are made.

## Metrics

The `/metrics` endpoint exposes Prometheus-compatible text output without requiring a metrics sidecar.

- `security_gateway_model_policy_info`: configured model metadata by sensitivity and reason requirement.
- `security_gateway_auth_events_total`: authentication attempts by method and outcome.
- `security_gateway_requests_total`: inference requests by `model_id` and `outcome`.
- `security_gateway_denials_total`: denied requests by `model_id` and policy or limiter reason.
- `security_gateway_input_tokens_total`: estimated input tokens by `model_id` and `outcome`.
- `security_gateway_inference_latency_seconds`: histogram, count, and sum for successful mock inference calls.

## Trace Export

Set `TRACE_EXPORT_PATH` or `OTEL_TRACE_EXPORT_PATH` to write one sanitized JSONL span per inference request. The exported record is OpenTelemetry-shaped for local review: service name, route, trace ID, span ID, parent span ID, outcome, auth method, model ID, estimated input-token count, configured token budget, and latency. It deliberately omits payload and identity fields that do not belong in public observability evidence.

The checked example in `artifacts/sanitized-trace-evidence.jsonl` shows the expected shape. `tests/test_trace_exporter.py` covers the privacy boundary.

Set `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` or `OTLP_TRACES_ENDPOINT` to send sanitized spans to an OTLP/HTTP collector. The local compose stack points the gateway at `http://otel-collector:4318/v1/traces` and uses `deploy/otel-collector/collector-config.yaml` for debug trace intake.

Run `python -m gateway.otlp_export --input artifacts/sanitized-trace-evidence.jsonl --output artifacts/otlp-collector-payload.json` to regenerate the checked collector payload without starting Docker. Add `--endpoint http://localhost:4318/v1/traces --send` when a local collector is running and you want to post the payload.

## Capacity Plan Artifact

Run `python -m gateway.capacity_plan --output artifacts/capacity-plan-evidence.json` to regenerate the checked synthetic capacity plan.

The report compares each configured model policy with a synthetic benchmark profile and records modeled request capacity, input-token capacity, decode-token capacity, p95 latency, target utilization, safety margin, and cost-to-serve estimates. The report is aggregate-only and intentionally excludes request bodies, decoded text, identities, secrets, and production logs.

Use the artifact before raising `requests_per_minute` or `input_tokens_per_minute`; if a policy would exceed modeled capacity, treat that as a rollout blocker until the benchmark profile or backend fleet shape is updated.

## Workload-Readiness Artifact

Run `python -m gateway.workload_replay --output artifacts/workload-readiness-evidence.json` to regenerate the checked synthetic workload-readiness report.

The replay drives aggregate synthetic traffic through the same role policy, request-limit, and token-budget logic used by the gateway. The report records outcome coverage, allowed-path p95 latency, model-level policy pressure, and release-gate status. It deliberately excludes request bodies, decoded text, identities, secrets, access reasons, and production logs.

Treat a `hold` readiness status as a blocker for local policy-limit changes until the missing guardrail path or latency regression is understood.

## Distributed Limiter Readiness Artifact

Run `python -m gateway.distributed_limiter --output artifacts/distributed-limiter-evidence.json` to regenerate the checked distributed-limiter report.

The report maps each configured model policy to request-count and estimated-input-token rules, records the Redis fixed-window atomic script hash, shows Envoy global-rate-limit descriptor shape, and checks rule coverage before a live external limiter is introduced. It is migration evidence only; the running demo still uses in-memory controls so reviewers can exercise the project without Redis, Envoy, cloud credentials, or production traffic.

Use the artifact before replacing the local limiter. Treat missing model coverage, missing token/request budget coverage, or a non-atomic script shape as a rollout blocker.

## Deployment Readiness Artifact

Run `python -m gateway.deployment_readiness --output artifacts/deployment-readiness-evidence.json` to regenerate the checked deployment-readiness report.

The report composes `artifacts/capacity-plan-evidence.json`, `artifacts/workload-readiness-evidence.json`, and `artifacts/distributed-limiter-evidence.json` into a synthetic release review. It records shadow, canary, staged rollout, and full rollout phases; per-model reserved capacity; rollback triggers; and release gates. It is deployment review evidence only and deliberately excludes request bodies, decoded text, identities, secrets, access reasons, and production logs.

Treat a `hold` deployment readiness status as a blocker for local serving-path changes until the underlying capacity, workload, limiter, phase-shape, or rollback gate is understood.

## Resilience Drill Artifact

Run `python -m gateway.resilience_drill --output artifacts/resilience-drill-evidence.json` to regenerate the checked resilience-drill report.

The report composes workload-readiness and deployment-readiness evidence with synthetic degradation probes. It records latency-spike, backend-error, queue-saturation, and audit-backpressure scenarios; per-probe detection signals; mitigation paths; rollback actions; and recovery gates. It is local resilience review evidence only and deliberately excludes request bodies, decoded text, identities, secrets, access reasons, and production logs.

Treat a `hold` resilience status as a blocker for local serving-path changes until the affected probe, source evidence, mitigation path, or rollback action is understood.

## Backend Probe Artifact

Run `python -m gateway.backend_probe --endpoint http://localhost:8001/v1 --requests 4 --output artifacts/backend-probe-evidence.json` after configuring an OpenAI-compatible completion endpoint. The probe uses the existing adapter, validates successful response shapes, and reports aggregate request count, success rate, latency percentiles, and reported token totals. It is intentionally sequential and bounded; the checked artifact excludes request bodies, decoded output, API keys, endpoint URLs, and principal identities.

Treat a `hold` probe status as evidence that the endpoint is not ready for measured capacity or resilience conclusions. Keep synthetic capacity and resilience artifacts as the review baseline until a real endpoint has been exercised repeatedly.

Run `python -m gateway.telemetry_snapshot --metrics-url http://localhost:8000/metrics --probe-report artifacts/backend-probe-evidence.json --output artifacts/telemetry-correlation-evidence.json` after the gateway has served the review traffic. The snapshot only keeps safe aggregate metric names and a whitelist of probe fields. Its latency values are histogram upper bounds rather than fabricated exact percentiles, and its release status is `hold` if the probe is held.

## Local Dashboard

`docker compose up --build` starts the gateway, OpenTelemetry Collector, Prometheus, and Grafana with the dashboard provisioned from `deploy/grafana/dashboards/security-gateway.json`.

Dashboard panels cover:

- request rate by outcome;
- estimated input-token throughput by outcome;
- p95 latency by model;
- authentication outcome rate;
- denial counts by model and reason;
- configured model-policy metadata.

## Alert Candidates

- Policy-denied traffic exceeds an expected baseline for a restricted model.
- JWT authentication failures spike by method or issuer rollout window.
- Request-count or token-budget limited traffic spikes for a single principal or model.
- p95 latency breaches the 500 ms objective for more than 10 minutes.
- Gateway health probe fails for 2 consecutive minutes.
- Audit log write errors appear in application logs.
- OTLP collector POST failures persist after a collector restart.
- Deployment-readiness gate reports `hold` before a model-policy or serving-path change.
- Resilience-drill gate reports `hold` before a serving-path or backend-routing change.

## Incident Runbooks

### Denied Access Spike

1. Check `security_gateway_auth_events_total` for failed JWT or disabled-demo-header traffic.
2. Check `security_gateway_denials_total` by model and reason.
3. Separate token validation failures, unknown principals, role mismatches, and missing reasons.
4. Review audit events for repeated principal/model pairs and shared trace IDs.
5. If traffic is abusive, block upstream identity or network source at the edge.
6. If traffic is legitimate, update role assignment through the identity provider before changing model policy.

### JWT Validation Failure

1. Confirm whether failures are `unexpected issuer`, `unexpected audience`, `token expired`, or `invalid jwt signature`.
2. Compare gateway `OIDC_ISSUER`, `OIDC_AUDIENCE`, and key material against the identity-provider rollout plan.
3. Use audit `trace_id` values to correlate rejected requests with upstream gateway or client logs.
4. Restore the previous issuer/audience/key configuration if a rollout caused a production denial spike.
5. Keep `ALLOW_DEMO_PRINCIPALS=false` outside local review environments.

### Trace Correlation Gap

1. Confirm responses include a `traceparent` header.
2. Check audit events for `trace_id`, `span_id`, and `parent_span_id`.
3. If `TRACE_EXPORT_PATH` is set, check the sanitized trace JSONL for the same `trace_id`.
4. If `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is set, verify the collector is reachable and accepts `POST /v1/traces`.
5. Regenerate `artifacts/otlp-collector-payload.json` from the checked trace sample to separate payload-shape problems from live collector reachability.
6. Verify upstream clients are forwarding a valid W3C `traceparent` header.
7. If missing, the gateway generates trace IDs; correlate from gateway audit outward.

### Rate Limit Spike

1. Check `security_gateway_requests_total{outcome="rate_limited"}` by model.
2. Compare the affected model policy to expected workload shape.
3. Confirm whether the principal is a batch client, interactive user, or synthetic test.
4. Raise the limit only after validating abuse risk and backend capacity.
5. Record the decision with model, principal, prior limit, new limit, and expiration date.

### Token Budget Spike

1. Check `security_gateway_requests_total{outcome="token_budget_limited"}` by model.
2. Compare `security_gateway_input_tokens_total` against the model's configured `input_tokens_per_minute`.
3. Review audit events for repeated principal/model pairs, estimated token counts, and shared trace IDs.
4. If traffic is abusive, block or throttle at the edge before raising the model budget.
5. If traffic is legitimate, regenerate `artifacts/capacity-plan-evidence.json` and raise the budget only with a capacity note and rollback time.

### Capacity Policy Change

1. Regenerate `artifacts/capacity-plan-evidence.json`.
2. Regenerate `artifacts/workload-readiness-evidence.json`.
3. Regenerate `artifacts/distributed-limiter-evidence.json` if the policy changes request or token budgets.
4. Regenerate `artifacts/deployment-readiness-evidence.json`.
5. Confirm the target model status is `within_synthetic_capacity`.
6. Confirm workload readiness remains `pass`.
7. Confirm distributed-limiter readiness remains `pass`.
8. Confirm deployment readiness remains `pass`.
9. Compare `policy_request_utilization`, `policy_input_token_utilization`, and `staged_capacity_utilization` against the intended rollout margin.
10. Keep the prior policy available for rollback if the change increases any utilization materially.
11. Record the policy diff, modeled capacity result, workload replay result, distributed limiter rule coverage, deployment-readiness phase, and rollback owner in the release note.

### Latency Regression

1. Compare latency buckets across models to isolate model-specific versus gateway-wide impact.
2. Check recent deploys, dependency changes, and backend routing changes.
3. Confirm CPU, memory, and event-loop saturation at the pod level.
4. Roll back if the regression follows a deploy and breaches the objective.
5. Add a regression test or benchmark case before reintroducing the change.

### Backend Degradation Drill

1. Regenerate `artifacts/workload-readiness-evidence.json`.
2. Regenerate `artifacts/deployment-readiness-evidence.json`.
3. Regenerate `artifacts/resilience-drill-evidence.json`.
4. Check whether the held probe is latency spike, backend error burst, queue saturation, or audit backpressure.
5. Confirm the mitigation path and rollback action are defined before changing traffic fraction or backend route.
6. Keep the prior route, limiter descriptors, and audit sink configuration available until the drill returns to `pass`.

## Deployment Checks

- `/health` must return `{"status":"ok"}` before routing production traffic.
- `/metrics` must include at least one `security_gateway_model_policy_info` sample.
- JWT deployments must set `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWT_HS256_SECRET`, and `ALLOW_DEMO_PRINCIPALS=false`.
- Local reviewer deployments may omit JWT settings and use `X-Principal-Id`.
- The audit log path must be writable by the container user.
- Kubernetes readiness and liveness probes should target `/health`.
- Prometheus should scrape `/metrics` on port 8000.
- Optional trace export path must point at a writable location and must not be treated as a prompt or output sink.
- Optional OTLP collector endpoint must point at a trusted collector and must only receive sanitized span attributes.
- Model policy changes should review both `requests_per_minute` and `input_tokens_per_minute`, then regenerate the capacity plan and workload-readiness artifacts.
- External limiter migrations should regenerate the distributed-limiter artifact and confirm every model has request-count and estimated-input-token coverage.
- Serving-path or policy rollout reviews should regenerate the deployment-readiness and resilience-drill artifacts and confirm capacity, workload, limiter, phase-shape, capacity-utilization, degradation-probe, and rollback gates remain `pass`.
