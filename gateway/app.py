from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from gateway.audit import JsonlAuditSink
from gateway.identity import AuthSettings, ResolvedPrincipal, resolve_principal
from gateway.metrics import GatewayMetrics
from gateway.mock_inference import run_mock_inference
from gateway.models import AuditEvent
from gateway.policy import evaluate_policy
from gateway.rate_limit import FixedWindowRateLimiter
from gateway.registry import MODEL_POLICIES
from gateway.trace_context import RequestTrace, format_traceparent, resolve_trace_context


class InferenceRequest(BaseModel):
    input: str = Field(min_length=1, max_length=4000)
    reason: str | None = Field(default=None, max_length=500)


class InferenceResponse(BaseModel):
    model_id: str
    output: str
    latency_ms: float
    backend: str
    traceparent: str
    audit: dict[str, object]


app = FastAPI(
    title="Secure GPU Inference Gateway",
    version="0.1.0",
    description="Public-safe AI inference security gateway demo.",
)

rate_limiter = FixedWindowRateLimiter()
audit_sink = JsonlAuditSink()
metrics = GatewayMetrics()
auth_settings = AuthSettings.from_env()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def prometheus_metrics() -> Response:
    return Response(
        metrics.render_prometheus(MODEL_POLICIES),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/v1/models")
def list_models() -> list[dict[str, object]]:
    return [
        {
            "model_id": policy.model_id,
            "description": policy.description,
            "sensitivity": policy.sensitivity,
            "requests_per_minute": policy.requests_per_minute,
            "requires_reason": policy.requires_reason,
        }
        for policy in MODEL_POLICIES.values()
    ]


@app.post("/v1/infer/{model_id}", response_model=InferenceResponse)
def infer(
    model_id: str,
    request: InferenceRequest,
    response: Response,
    x_principal_id: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    traceparent: str | None = Header(default=None),
) -> InferenceResponse:
    trace = resolve_trace_context(traceparent)
    response.headers["traceparent"] = format_traceparent(trace)
    resolved = resolve_principal(authorization, x_principal_id, auth_settings)
    principal = resolved.principal
    model_policy = MODEL_POLICIES.get(model_id)
    decision = evaluate_policy(principal, model_policy, request.reason)
    decision_reasons = with_auth_failure(decision.reasons, resolved.failure_reason)
    metrics.record_auth_event(
        resolved.auth_method,
        "accepted" if resolved.principal else "failed",
    )

    if not decision.allowed:
        metrics.record_request(model_id, "policy_denied", decision_reasons)
        audit_sink.write(
            audit_event(
                resolved,
                trace,
                model_id=model_id,
                allowed=False,
                reason=request.reason,
                decision_reasons=decision_reasons,
            )
        )
        raise HTTPException(
            status_code=403,
            detail={"reasons": decision_reasons, "trace_id": trace.trace_id},
            headers={"traceparent": format_traceparent(trace)},
        )

    assert principal is not None
    assert model_policy is not None

    if not rate_limiter.allow(principal.principal_id, model_id, model_policy.requests_per_minute):
        metrics.record_request(model_id, "rate_limited", ("rate limit exceeded",))
        audit_sink.write(
            audit_event(
                resolved,
                trace,
                model_id=model_id,
                allowed=False,
                reason=request.reason,
                decision_reasons=("rate limit exceeded",),
            )
        )
        raise HTTPException(
            status_code=429,
            detail={"reason": "rate limit exceeded", "trace_id": trace.trace_id},
            headers={"traceparent": format_traceparent(trace)},
        )

    result = run_mock_inference(model_id, request.input)
    latency_ms = float(result["latency_ms"])
    metrics.record_request(model_id, "allowed")
    metrics.observe_latency(model_id, latency_ms / 1000)
    audit_sink.write(
        audit_event(
            resolved,
            trace,
            model_id=model_id,
            allowed=True,
            reason=request.reason,
            decision_reasons=decision.reasons,
            latency_ms=latency_ms,
        )
    )

    return InferenceResponse(
        model_id=str(result["model_id"]),
        output=str(result["output"]),
        latency_ms=latency_ms,
        backend=str(result["backend"]),
        traceparent=format_traceparent(trace),
        audit={
            "principal_id": principal.principal_id,
            "auth_method": resolved.auth_method,
            "decision": "allow",
            "decision_reasons": decision.reasons,
            "trace_id": trace.trace_id,
            "span_id": trace.span_id,
        },
    )


def with_auth_failure(
    decision_reasons: tuple[str, ...],
    failure_reason: str | None,
) -> tuple[str, ...]:
    if failure_reason:
        return (f"authentication failed: {failure_reason}", *decision_reasons)
    return decision_reasons


def audit_event(
    resolved: ResolvedPrincipal,
    trace: RequestTrace,
    *,
    model_id: str,
    allowed: bool,
    reason: str | None,
    decision_reasons: tuple[str, ...],
    latency_ms: float | None = None,
) -> AuditEvent:
    principal_id = (
        resolved.principal.principal_id
        if resolved.principal
        else resolved.subject or "unknown"
    )
    return AuditEvent(
        principal_id=principal_id,
        model_id=model_id,
        allowed=allowed,
        reason=reason,
        decision_reasons=decision_reasons,
        latency_ms=latency_ms,
        auth_method=resolved.auth_method,
        auth_subject=resolved.subject,
        auth_issuer=resolved.issuer,
        trace_id=trace.trace_id,
        span_id=trace.span_id,
        parent_span_id=trace.parent_span_id,
    )
