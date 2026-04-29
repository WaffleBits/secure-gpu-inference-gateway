from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from gateway.audit import JsonlAuditSink
from gateway.metrics import GatewayMetrics
from gateway.mock_inference import run_mock_inference
from gateway.models import AuditEvent
from gateway.policy import evaluate_policy
from gateway.rate_limit import FixedWindowRateLimiter
from gateway.registry import MODEL_POLICIES, PRINCIPALS


class InferenceRequest(BaseModel):
    input: str = Field(min_length=1, max_length=4000)
    reason: str | None = Field(default=None, max_length=500)


class InferenceResponse(BaseModel):
    model_id: str
    output: str
    latency_ms: float
    backend: str
    audit: dict[str, object]


app = FastAPI(
    title="Secure GPU Inference Gateway",
    version="0.1.0",
    description="Public-safe AI inference security gateway demo.",
)

rate_limiter = FixedWindowRateLimiter()
audit_sink = JsonlAuditSink()
metrics = GatewayMetrics()


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
    x_principal_id: str | None = Header(default=None),
) -> InferenceResponse:
    principal = PRINCIPALS.get(x_principal_id or "")
    model_policy = MODEL_POLICIES.get(model_id)
    decision = evaluate_policy(principal, model_policy, request.reason)

    if not decision.allowed:
        metrics.record_request(model_id, "policy_denied", decision.reasons)
        audit_sink.write(
            AuditEvent(
                principal_id=x_principal_id or "unknown",
                model_id=model_id,
                allowed=False,
                reason=request.reason,
                decision_reasons=decision.reasons,
            )
        )
        raise HTTPException(status_code=403, detail={"reasons": decision.reasons})

    assert principal is not None
    assert model_policy is not None

    if not rate_limiter.allow(principal.principal_id, model_id, model_policy.requests_per_minute):
        metrics.record_request(model_id, "rate_limited", ("rate limit exceeded",))
        audit_sink.write(
            AuditEvent(
                principal_id=principal.principal_id,
                model_id=model_id,
                allowed=False,
                reason=request.reason,
                decision_reasons=("rate limit exceeded",),
            )
        )
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    result = run_mock_inference(model_id, request.input)
    latency_ms = float(result["latency_ms"])
    metrics.record_request(model_id, "allowed")
    metrics.observe_latency(model_id, latency_ms / 1000)
    audit_sink.write(
        AuditEvent(
            principal_id=principal.principal_id,
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
        audit={
            "principal_id": principal.principal_id,
            "decision": "allow",
            "decision_reasons": decision.reasons,
        },
    )
