from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    principal_id: str
    display_name: str
    roles: frozenset[str]


@dataclass(frozen=True)
class ModelPolicy:
    model_id: str
    description: str
    allowed_roles: frozenset[str]
    requests_per_minute: int
    input_tokens_per_minute: int
    requires_reason: bool = True
    sensitivity: str = "standard"


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AuditEvent:
    principal_id: str
    model_id: str
    allowed: bool
    reason: str | None
    decision_reasons: tuple[str, ...]
    latency_ms: float | None = None
    auth_method: str | None = None
    auth_subject: str | None = None
    auth_issuer: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    estimated_input_tokens: int | None = None
    token_budget_limit: int | None = None
