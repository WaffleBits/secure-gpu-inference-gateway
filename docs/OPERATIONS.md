# Operations Notes

This service is intentionally small, but it is shaped like an AI infrastructure control plane that can be operated under real reliability and security expectations.

## Service Level Objectives

- Availability: 99.9% successful gateway responses for authorized traffic over 30 days.
- Latency: p95 successful inference gateway latency below 500 ms for the mock backend.
- Policy correctness: 100% of denied requests include a structured decision reason in audit logs and metrics.
- Audit durability: every allowed, policy-denied, and rate-limited inference request writes one audit event.
- Traceability: every inference response and audit event carries a trace ID for request correlation.

## Metrics

The `/metrics` endpoint exposes Prometheus-compatible text output without requiring a metrics sidecar.

- `security_gateway_model_policy_info`: configured model metadata by sensitivity and reason requirement.
- `security_gateway_auth_events_total`: authentication attempts by method and outcome.
- `security_gateway_requests_total`: inference requests by `model_id` and `outcome`.
- `security_gateway_denials_total`: denied requests by `model_id` and policy or limiter reason.
- `security_gateway_inference_latency_seconds`: histogram, count, and sum for successful mock inference calls.

## Alert Candidates

- Policy-denied traffic exceeds an expected baseline for a restricted model.
- JWT authentication failures spike by method or issuer rollout window.
- Rate-limited traffic spikes for a single principal or model.
- p95 latency breaches the 500 ms objective for more than 10 minutes.
- Gateway health probe fails for 2 consecutive minutes.
- Audit log write errors appear in application logs.

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
3. Verify upstream clients are forwarding a valid W3C `traceparent` header.
4. If missing, the gateway generates trace IDs; correlate from gateway audit outward.

### Rate Limit Spike

1. Check `security_gateway_requests_total{outcome="rate_limited"}` by model.
2. Compare the affected model policy to expected workload shape.
3. Confirm whether the principal is a batch client, interactive user, or synthetic test.
4. Raise the limit only after validating abuse risk and backend capacity.
5. Record the decision with model, principal, prior limit, new limit, and expiration date.

### Latency Regression

1. Compare latency buckets across models to isolate model-specific versus gateway-wide impact.
2. Check recent deploys, dependency changes, and backend routing changes.
3. Confirm CPU, memory, and event-loop saturation at the pod level.
4. Roll back if the regression follows a deploy and breaches the objective.
5. Add a regression test or benchmark case before reintroducing the change.

## Deployment Checks

- `/health` must return `{"status":"ok"}` before routing production traffic.
- `/metrics` must include at least one `security_gateway_model_policy_info` sample.
- JWT deployments must set `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWT_HS256_SECRET`, and `ALLOW_DEMO_PRINCIPALS=false`.
- Local reviewer deployments may omit JWT settings and use `X-Principal-Id`.
- The audit log path must be writable by the container user.
- Kubernetes readiness and liveness probes should target `/health`.
- Prometheus should scrape `/metrics` on port 8000.
