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

