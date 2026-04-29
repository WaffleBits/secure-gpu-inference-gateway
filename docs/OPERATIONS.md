# Operations Notes

This service is intentionally small, but it is shaped like an AI infrastructure control plane that can be operated under real reliability and security expectations.

## Service Level Objectives

- Availability: 99.9% successful gateway responses for authorized traffic over 30 days.
- Latency: p95 successful inference gateway latency below 500 ms for the mock backend.
- Policy correctness: 100% of denied requests include a structured decision reason in audit logs and metrics.
- Audit durability: every allowed, policy-denied, and rate-limited inference request writes one audit event.

## Metrics

The `/metrics` endpoint exposes Prometheus-compatible text output without requiring a metrics sidecar.

- `security_gateway_model_policy_info`: configured model metadata by sensitivity and reason requirement.
- `security_gateway_requests_total`: inference requests by `model_id` and `outcome`.
- `security_gateway_denials_total`: denied requests by `model_id` and policy or limiter reason.
- `security_gateway_inference_latency_seconds`: histogram, count, and sum for successful mock inference calls.

## Alert Candidates

- Policy-denied traffic exceeds an expected baseline for a restricted model.
- Rate-limited traffic spikes for a single principal or model.
- p95 latency breaches the 500 ms objective for more than 10 minutes.
- Gateway health probe fails for 2 consecutive minutes.
- Audit log write errors appear in application logs.

## Incident Runbooks

### Denied Access Spike

1. Check `security_gateway_denials_total` by model and reason.
2. Separate unknown principals from role mismatches and missing reasons.
3. Review audit events for repeated principal/model pairs.
4. If traffic is abusive, block upstream identity or network source at the edge.
5. If traffic is legitimate, update role assignment through the identity provider before changing model policy.

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
- The audit log path must be writable by the container user.
- Kubernetes readiness and liveness probes should target `/health`.
- Prometheus should scrape `/metrics` on port 8000.
