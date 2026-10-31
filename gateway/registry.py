from __future__ import annotations

import os

from gateway.models import ModelPolicy, Principal


def _positive_env_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


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
        input_tokens_per_minute=8000,
        requires_reason=True,
        sensitivity="standard",
    ),
    "threat-triage": ModelPolicy(
        model_id="threat-triage",
        description="Synthetic security event triage model",
        allowed_roles=frozenset({"security-engineer", "platform-admin"}),
        requests_per_minute=20,
        input_tokens_per_minute=6000,
        requires_reason=True,
        sensitivity="restricted",
    ),
    "benchmark-echo": ModelPolicy(
        model_id="benchmark-echo",
        description="Low-risk model used for load and health checks",
        allowed_roles=frozenset({"analyst", "security-engineer", "platform-admin"}),
        requests_per_minute=_positive_env_int(
            "BENCHMARK_REQUESTS_PER_MINUTE",
            120,
        ),
        input_tokens_per_minute=_positive_env_int(
            "BENCHMARK_INPUT_TOKENS_PER_MINUTE",
            20000,
        ),
        requires_reason=False,
        sensitivity="low",
    ),
}
