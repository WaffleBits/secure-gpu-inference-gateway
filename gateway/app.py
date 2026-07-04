from __future__ import annotations

from time import time_ns

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from gateway.audit import JsonlAuditSink
from gateway.identity import AuthSettings, ResolvedPrincipal, resolve_principal
from gateway.metrics import GatewayMetrics
from gateway.mock_inference import run_mock_inference
from gateway.models import AuditEvent
from gateway.policy import evaluate_policy
from gateway.rate_limit import FixedWindowRateLimiter, FixedWindowTokenBudgetLimiter
from gateway.registry import MODEL_POLICIES
from gateway.token_budget import estimate_input_tokens
from gateway.trace_context import RequestTrace, format_traceparent, resolve_trace_context
from gateway.trace_exporter import build_trace_exporter_from_env


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
token_budget_limiter = FixedWindowTokenBudgetLimiter()
audit_sink = JsonlAuditSink()
metrics = GatewayMetrics()
auth_settings = AuthSettings.from_env()
trace_exporter = build_trace_exporter_from_env()


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
            "input_tokens_per_minute": policy.input_tokens_per_minute,
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
    started_at_unix_nano = time_ns()
    trace = resolve_trace_context(traceparent)
    response.headers["traceparent"] = format_traceparent(trace)
    resolved = resolve_principal(authorization, x_principal_id, auth_settings)
    principal = resolved.principal
    model_policy = MODEL_POLICIES.get(model_id)
    estimated_input_tokens = estimate_input_tokens(request.input)
    token_budget_limit = (
        model_policy.input_tokens_per_minute if model_policy is not None else None
    )
    decision = evaluate_policy(principal, model_policy, request.reason)
    decision_reasons = with_auth_failure(decision.reasons, resolved.failure_reason)
    metrics.record_auth_event(
        resolved.auth_method,
        "accepted" if resolved.principal else "failed",
    )

    if not decision.allowed:
        metrics.record_request(model_id, "policy_denied", decision_reasons)
        metrics.record_input_tokens(model_id, "policy_denied", estimated_input_tokens)
        audit_sink.write(
            audit_event(
                resolved,
                trace,
                model_id=model_id,
                allowed=False,
                reason=request.reason,
                decision_reasons=decision_reasons,
                estimated_input_tokens=estimated_input_tokens,
                token_budget_limit=token_budget_limit,
            )
        )
        export_trace_span(
            trace,
            model_id=model_id,
            outcome="policy_denied",
            auth_method=resolved.auth_method,
            decision_reasons=decision_reasons,
            latency_ms=None,
            estimated_input_tokens=estimated_input_tokens,
            token_budget_limit=token_budget_limit,
            started_at_unix_nano=started_at_unix_nano,
        )
        raise HTTPException(
            status_code=403,
            detail={"reasons": decision_reasons, "trace_id": trace.trace_id},
            headers={"traceparent": format_traceparent(trace)},
        )

    assert principal is not None
    assert model_policy is not None

    if not rate_limiter.allow(
        principal.principal_id,
        model_id,
        model_policy.requests_per_minute,
    ):
        metrics.record_request(model_id, "rate_limited", ("rate limit exceeded",))
        metrics.record_input_tokens(model_id, "rate_limited", estimated_input_tokens)
        audit_sink.write(
            audit_event(
                resolved,
                trace,
                model_id=model_id,
                allowed=False,
                reason=request.reason,
                decision_reasons=("rate limit exceeded",),
                estimated_input_tokens=estimated_input_tokens,
                token_budget_limit=token_budget_limit,
            )
        )
        export_trace_span(
            trace,
            model_id=model_id,
            outcome="rate_limited",
            auth_method=resolved.auth_method,
            decision_reasons=("rate limit exceeded",),
            latency_ms=None,
            estimated_input_tokens=estimated_input_tokens,
            token_budget_limit=token_budget_limit,
            started_at_unix_nano=started_at_unix_nano,
        )
        raise HTTPException(
            status_code=429,
            detail={"reason": "rate limit exceeded", "trace_id": trace.trace_id},
            headers={"traceparent": format_traceparent(trace)},
        )

    if not token_budget_limiter.allow(
        principal.principal_id,
        model_id,
        estimated_input_tokens,
        model_policy.input_tokens_per_minute,
    ):
        metrics.record_request(
            model_id,
            "token_budget_limited",
            ("token budget exceeded",),
        )
        metrics.record_input_tokens(
            model_id,
            "token_budget_limited",
            estimated_input_tokens,
        )
        audit_sink.write(
            audit_event(
                resolved,
                trace,
                model_id=model_id,
                allowed=False,
                reason=request.reason,
                decision_reasons=("token budget exceeded",),
                estimated_input_tokens=estimated_input_tokens,
                token_budget_limit=token_budget_limit,
            )
        )
        export_trace_span(
            trace,
            model_id=model_id,
            outcome="token_budget_limited",
            auth_method=resolved.auth_method,
            decision_reasons=("token budget exceeded",),
            latency_ms=None,
            estimated_input_tokens=estimated_input_tokens,
            token_budget_limit=token_budget_limit,
            started_at_unix_nano=started_at_unix_nano,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "reason": "token budget exceeded",
                "trace_id": trace.trace_id,
                "estimated_input_tokens": estimated_input_tokens,
                "input_tokens_per_minute": model_policy.input_tokens_per_minute,
            },
            headers={"traceparent": format_traceparent(trace)},
        )

    result = run_mock_inference(model_id, request.input)
    latency_ms = float(result["latency_ms"])
    metrics.record_request(model_id, "allowed")
    metrics.record_input_tokens(model_id, "allowed", estimated_input_tokens)
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
            estimated_input_tokens=estimated_input_tokens,
            token_budget_limit=token_budget_limit,
        )
    )
    export_trace_span(
        trace,
        model_id=model_id,
        outcome="allowed",
        auth_method=resolved.auth_method,
        decision_reasons=decision.reasons,
        latency_ms=latency_ms,
        estimated_input_tokens=estimated_input_tokens,
        token_budget_limit=token_budget_limit,
        started_at_unix_nano=started_at_unix_nano,
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
            "estimated_input_tokens": estimated_input_tokens,
            "token_budget_limit": token_budget_limit,
        },
    )


def export_trace_span(
    trace: RequestTrace,
    *,
    model_id: str,
    outcome: str,
    auth_method: str,
    decision_reasons: tuple[str, ...],
    latency_ms: float | None,
    estimated_input_tokens: int,
    token_budget_limit: int | None,
    started_at_unix_nano: int,
) -> None:
    if trace_exporter is None:
        return

    trace_exporter.write_span(
        trace,
        model_id=model_id,
        outcome=outcome,
        auth_method=auth_method,
        decision_reasons=decision_reasons,
        latency_ms=latency_ms,
        started_at_unix_nano=started_at_unix_nano,
        ended_at_unix_nano=time_ns(),
        extra_attributes=trace_budget_attributes(
            estimated_input_tokens,
            token_budget_limit,
        ),
    )


def trace_budget_attributes(
    estimated_input_tokens: int,
    token_budget_limit: int | None,
) -> dict[str, int]:
    attributes = {
        "ai.gateway.estimated_input_tokens": estimated_input_tokens,
    }
    if token_budget_limit is not None:
        attributes["ai.gateway.token_budget_limit"] = token_budget_limit
    return attributes


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
    estimated_input_tokens: int | None = None,
    token_budget_limit: int | None = None,
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
        estimated_input_tokens=estimated_input_tokens,
        token_budget_limit=token_budget_limit,
    )
