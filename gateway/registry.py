from __future__ import annotations

from gateway.models import ModelPolicy, Principal


PRINCIPALS = {
    "analyst-1": Principal(
        principal_id="analyst-1",
        display_name="Mission Analyst",
        roles=frozenset({"analyst", "mission-user"}),
    ),
    "security-1": Principal(
        principal_id="security-1",
        display_name="Security Engineer",
        roles=frozenset({"security-engineer", "mission-user"}),
    ),
    "admin-1": Principal(
        principal_id="admin-1",
        display_name="Platform Admin",
        roles=frozenset({"platform-admin", "security-engineer"}),
    ),
}


MODEL_POLICIES = {
    "mission-summarizer": ModelPolicy(
        model_id="mission-summarizer",
        description="Synthetic mission report summarization model",
        allowed_roles=frozenset({"analyst", "mission-user", "platform-admin"}),
        requests_per_minute=30,
        requires_reason=True,
        sensitivity="standard",
    ),
    "threat-triage": ModelPolicy(
        model_id="threat-triage",
        description="Synthetic security event triage model",
        allowed_roles=frozenset({"security-engineer", "platform-admin"}),
        requests_per_minute=20,
        requires_reason=True,
        sensitivity="restricted",
    ),
    "benchmark-echo": ModelPolicy(
        model_id="benchmark-echo",
        description="Low-risk model used for load and health checks",
        allowed_roles=frozenset({"analyst", "security-engineer", "platform-admin"}),
        requests_per_minute=120,
        requires_reason=False,
        sensitivity="low",
    ),
}

