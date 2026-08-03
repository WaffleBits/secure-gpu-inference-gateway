from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter, time_ns
from typing import Iterator

import httpx
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from gateway.audit import JsonlAuditSink
from gateway.backend_adapter import (
    BackendAdapterError,
    open_completion_response,
    run_configured_inference,
)
from gateway.identity import AuthSettings, ResolvedPrincipal, resolve_principal
from gateway.metrics import GatewayMetrics
from gateway.models import AuditEvent
from gateway.policy import evaluate_policy
from gateway.rate_limit import build_limiters_from_env, limiter_backend_name
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


class CompletionStreamOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_usage: bool = True
    continuous_usage_stats: bool | None = None


class CompletionRequest(BaseModel):
    """Bounded OpenAI-compatible completion shape used by the real proxy path."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=32768)
    max_tokens: int = Field(default=16, ge=1, le=4096)
    stream: bool = True
    stream_options: CompletionStreamOptions | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=-1)
    min_p: float | None = Field(default=None, ge=0.0, le=1.0)
    repetition_penalty: float | None = Field(default=None, gt=0.0)
    logprobs: int | None = Field(default=None, ge=0, le=20)
    seed: int | None = None
    ignore_eos: bool | None = None
    stop: str | list[str] | None = None
    echo: bool | None = None
    n: int = Field(default=1, ge=1, le=1)


@dataclass(frozen=True)
class CompletionAdmission:
    resolved: ResolvedPrincipal
    trace: RequestTrace
    model_id: str
    reason: str | None
    decision_reasons: tuple[str, ...]
    estimated_input_tokens: int
    token_budget_limit: int
    started_at_unix_nano: int
    started_at_perf: float
    request_limiter_ms: float
    token_limiter_ms: float


app = FastAPI(
    title="Secure GPU Inference Gateway",
    version="0.1.0",
    description="Public-safe AI inference security gateway demo.",
)

rate_limiter, token_budget_limiter = build_limiters_from_env()
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

    limiter_started = perf_counter()
    request_allowed = rate_limiter.allow(
        principal.principal_id,
        model_id,
        model_policy.requests_per_minute,
    )
    metrics.observe_limiter_latency(
        "requests",
        limiter_backend_name(rate_limiter),
        perf_counter() - limiter_started,
    )
    if not request_allowed:
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

    limiter_started = perf_counter()
    token_budget_allowed = token_budget_limiter.allow(
        principal.principal_id,
        model_id,
        estimated_input_tokens,
        model_policy.input_tokens_per_minute,
    )
    metrics.observe_limiter_latency(
        "input_tokens",
        limiter_backend_name(token_budget_limiter),
        perf_counter() - limiter_started,
    )
    if not token_budget_allowed:
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

    try:
        result = run_configured_inference(model_id, request.input)
    except BackendAdapterError:
        backend_error_reasons = ("inference backend unavailable",)
        metrics.record_request(model_id, "backend_error", backend_error_reasons)
        metrics.record_input_tokens(model_id, "backend_error", estimated_input_tokens)
        audit_sink.write(
            audit_event(
                resolved,
                trace,
                model_id=model_id,
                allowed=True,
                reason=request.reason,
                decision_reasons=backend_error_reasons,
                estimated_input_tokens=estimated_input_tokens,
                token_budget_limit=token_budget_limit,
            )
        )
        export_trace_span(
            trace,
            model_id=model_id,
            outcome="backend_error",
            auth_method=resolved.auth_method,
            decision_reasons=backend_error_reasons,
            latency_ms=None,
            estimated_input_tokens=estimated_input_tokens,
            token_budget_limit=token_budget_limit,
            started_at_unix_nano=started_at_unix_nano,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "reason": "inference backend unavailable",
                "trace_id": trace.trace_id,
            },
            headers={"traceparent": format_traceparent(trace)},
        )

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


@app.post("/v1/completions")
def completions(
    request: CompletionRequest,
    authorization: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
    x_inference_reason: str | None = Header(default=None),
    traceparent: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
) -> Response:
    """Apply the full control path and proxy an OpenAI-compatible completion."""
    admission = admit_completion(
        model_id=request.model,
        prompt=request.prompt,
        reason=x_inference_reason,
        authorization=authorization,
        demo_principal_id=x_principal_id,
        traceparent=traceparent,
    )
    payload = request.model_dump(mode="json", exclude_unset=True)
    admission_ms = (perf_counter() - admission.started_at_perf) * 1000

    upstream_started = perf_counter()
    try:
        backend_response = open_completion_response(
            payload,
            request_id=x_request_id,
        )
    except BackendAdapterError:
        record_completion_event(
            admission,
            outcome="backend_error",
            allowed=True,
            decision_reasons=("inference backend unavailable",),
            latency_ms=(perf_counter() - admission.started_at_perf) * 1000,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "reason": "inference backend unavailable",
                "trace_id": admission.trace.trace_id,
            },
            headers=completion_response_headers(admission),
        )
    upstream_headers_ms = (perf_counter() - upstream_started) * 1000

    headers = completion_response_headers(
        admission,
        admission_ms=admission_ms,
        upstream_headers_ms=upstream_headers_ms,
    )
    for header_name in ("cache-control", "content-type", "x-request-id"):
        value = backend_response.headers.get(header_name)
        if value:
            headers[header_name] = value

    if request.stream:
        return StreamingResponse(
            iter_completion_response(backend_response, admission),
            status_code=backend_response.status_code,
            headers=headers,
        )

    try:
        body = backend_response.read()
    except httpx.HTTPError:
        backend_response.close()
        record_completion_event(
            admission,
            outcome="backend_error",
            allowed=True,
            decision_reasons=("inference backend unavailable",),
            latency_ms=(perf_counter() - admission.started_at_perf) * 1000,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "reason": "inference backend unavailable",
                "trace_id": admission.trace.trace_id,
            },
            headers=headers,
        )
    finally:
        backend_response.close()

    latency_ms = (perf_counter() - admission.started_at_perf) * 1000
    record_completion_event(
        admission,
        outcome="allowed",
        allowed=True,
        decision_reasons=admission.decision_reasons,
        latency_ms=latency_ms,
    )
    return Response(
        content=body,
        status_code=backend_response.status_code,
        headers=headers,
    )


def admit_completion(
    *,
    model_id: str,
    prompt: str,
    reason: str | None,
    authorization: str | None,
    demo_principal_id: str | None,
    traceparent: str | None,
) -> CompletionAdmission:
    started_at_unix_nano = time_ns()
    started_at_perf = perf_counter()
    trace = resolve_trace_context(traceparent)
    resolved = resolve_principal(authorization, demo_principal_id, auth_settings)
    principal = resolved.principal
    model_policy = MODEL_POLICIES.get(model_id)
    estimated_input_tokens = estimate_input_tokens(prompt)
    token_budget_limit = (
        model_policy.input_tokens_per_minute if model_policy is not None else None
    )
    decision = evaluate_policy(principal, model_policy, reason)
    decision_reasons = with_auth_failure(decision.reasons, resolved.failure_reason)
    metrics.record_auth_event(
        resolved.auth_method,
        "accepted" if resolved.principal else "failed",
    )

    if not decision.allowed:
        record_completion_event_fields(
            resolved=resolved,
            trace=trace,
            model_id=model_id,
            outcome="policy_denied",
            allowed=False,
            reason=reason,
            decision_reasons=decision_reasons,
            estimated_input_tokens=estimated_input_tokens,
            token_budget_limit=token_budget_limit,
            latency_ms=None,
            started_at_unix_nano=started_at_unix_nano,
        )
        raise HTTPException(
            status_code=403,
            detail={"reasons": decision_reasons, "trace_id": trace.trace_id},
            headers={"traceparent": format_traceparent(trace)},
        )

    assert principal is not None
    assert model_policy is not None
    assert token_budget_limit is not None

    limiter_started = perf_counter()
    request_allowed = rate_limiter.allow(
        principal.principal_id,
        model_id,
        model_policy.requests_per_minute,
    )
    request_limiter_ms = (perf_counter() - limiter_started) * 1000
    metrics.observe_limiter_latency(
        "requests",
        limiter_backend_name(rate_limiter),
        request_limiter_ms / 1000,
    )
    if not request_allowed:
        reasons = ("rate limit exceeded",)
        record_completion_event_fields(
            resolved=resolved,
            trace=trace,
            model_id=model_id,
            outcome="rate_limited",
            allowed=False,
            reason=reason,
            decision_reasons=reasons,
            estimated_input_tokens=estimated_input_tokens,
            token_budget_limit=token_budget_limit,
            latency_ms=None,
            started_at_unix_nano=started_at_unix_nano,
        )
        raise HTTPException(
            status_code=429,
            detail={"reason": reasons[0], "trace_id": trace.trace_id},
            headers={
                "traceparent": format_traceparent(trace),
                "server-timing": f"request-limit;dur={request_limiter_ms:.3f}",
            },
        )

    limiter_started = perf_counter()
    token_allowed = token_budget_limiter.allow(
        principal.principal_id,
        model_id,
        estimated_input_tokens,
        model_policy.input_tokens_per_minute,
    )
    token_limiter_ms = (perf_counter() - limiter_started) * 1000
    metrics.observe_limiter_latency(
        "input_tokens",
        limiter_backend_name(token_budget_limiter),
        token_limiter_ms / 1000,
    )
    if not token_allowed:
        reasons = ("token budget exceeded",)
        record_completion_event_fields(
            resolved=resolved,
            trace=trace,
            model_id=model_id,
            outcome="token_budget_limited",
            allowed=False,
            reason=reason,
            decision_reasons=reasons,
            estimated_input_tokens=estimated_input_tokens,
            token_budget_limit=token_budget_limit,
            latency_ms=None,
            started_at_unix_nano=started_at_unix_nano,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "reason": reasons[0],
                "trace_id": trace.trace_id,
                "estimated_input_tokens": estimated_input_tokens,
                "input_tokens_per_minute": token_budget_limit,
            },
            headers={
                "traceparent": format_traceparent(trace),
                "server-timing": (
                    f"request-limit;dur={request_limiter_ms:.3f}, "
                    f"token-budget;dur={token_limiter_ms:.3f}"
                ),
            },
        )

    return CompletionAdmission(
        resolved=resolved,
        trace=trace,
        model_id=model_id,
        reason=reason,
        decision_reasons=decision_reasons,
        estimated_input_tokens=estimated_input_tokens,
        token_budget_limit=token_budget_limit,
        started_at_unix_nano=started_at_unix_nano,
        started_at_perf=started_at_perf,
        request_limiter_ms=request_limiter_ms,
        token_limiter_ms=token_limiter_ms,
    )


def iter_completion_response(
    backend_response: httpx.Response,
    admission: CompletionAdmission,
) -> Iterator[bytes]:
    outcome = "allowed"
    decision_reasons = admission.decision_reasons
    try:
        for chunk in backend_response.iter_raw():
            if chunk:
                yield chunk
    except httpx.HTTPError:
        outcome = "backend_error"
        decision_reasons = ("inference backend unavailable",)
        raise
    except GeneratorExit:
        outcome = "backend_error"
        decision_reasons = ("client disconnected during inference",)
        raise
    finally:
        backend_response.close()
        record_completion_event(
            admission,
            outcome=outcome,
            allowed=True,
            decision_reasons=decision_reasons,
            latency_ms=(perf_counter() - admission.started_at_perf) * 1000,
        )


def completion_response_headers(
    admission: CompletionAdmission,
    *,
    admission_ms: float | None = None,
    upstream_headers_ms: float | None = None,
) -> dict[str, str]:
    timing_parts = [
        f"request-limit;dur={admission.request_limiter_ms:.3f}",
        f"token-budget;dur={admission.token_limiter_ms:.3f}",
    ]
    if admission_ms is not None:
        timing_parts.insert(0, f"gateway-admission;dur={admission_ms:.3f}")
    if upstream_headers_ms is not None:
        timing_parts.append(f"upstream-headers;dur={upstream_headers_ms:.3f}")
    return {
        "traceparent": format_traceparent(admission.trace),
        "server-timing": ", ".join(timing_parts),
    }


def record_completion_event(
    admission: CompletionAdmission,
    *,
    outcome: str,
    allowed: bool,
    decision_reasons: tuple[str, ...],
    latency_ms: float | None,
) -> None:
    record_completion_event_fields(
        resolved=admission.resolved,
        trace=admission.trace,
        model_id=admission.model_id,
        outcome=outcome,
        allowed=allowed,
        reason=admission.reason,
        decision_reasons=decision_reasons,
        estimated_input_tokens=admission.estimated_input_tokens,
        token_budget_limit=admission.token_budget_limit,
        latency_ms=latency_ms,
        started_at_unix_nano=admission.started_at_unix_nano,
    )


def record_completion_event_fields(
    *,
    resolved: ResolvedPrincipal,
    trace: RequestTrace,
    model_id: str,
    outcome: str,
    allowed: bool,
    reason: str | None,
    decision_reasons: tuple[str, ...],
    estimated_input_tokens: int,
    token_budget_limit: int | None,
    latency_ms: float | None,
    started_at_unix_nano: int,
) -> None:
    metrics.record_request(model_id, outcome, decision_reasons)
    metrics.record_input_tokens(model_id, outcome, estimated_input_tokens)
    if outcome == "allowed" and latency_ms is not None:
        metrics.observe_latency(model_id, latency_ms / 1000)
    audit_sink.write(
        audit_event(
            resolved,
            trace,
            model_id=model_id,
            allowed=allowed,
            reason=reason,
            decision_reasons=decision_reasons,
            latency_ms=latency_ms,
            estimated_input_tokens=estimated_input_tokens,
            token_budget_limit=token_budget_limit,
        )
    )
    export_trace_span(
        trace,
        model_id=model_id,
        outcome=outcome,
        auth_method=resolved.auth_method,
        decision_reasons=decision_reasons,
        latency_ms=latency_ms,
        estimated_input_tokens=estimated_input_tokens,
        token_budget_limit=token_budget_limit,
        started_at_unix_nano=started_at_unix_nano,
        route="/v1/completions",
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
    route: str = "/v1/infer/{model_id}",
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
        route=route,
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
