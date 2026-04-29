from __future__ import annotations

from gateway.models import ModelPolicy, PolicyDecision, Principal


def evaluate_policy(
    principal: Principal | None,
    model_policy: ModelPolicy | None,
    reason: str | None,
) -> PolicyDecision:
    reasons: list[str] = []

    if principal is None:
        reasons.append("unknown principal")
        return PolicyDecision(False, tuple(reasons))

    if model_policy is None:
        reasons.append("unknown model")
        return PolicyDecision(False, tuple(reasons))

    if principal.roles.isdisjoint(model_policy.allowed_roles):
        reasons.append("principal lacks an allowed role for this model")

    if model_policy.requires_reason and not (reason and reason.strip()):
        reasons.append("reason is required for this model")

    if reasons:
        return PolicyDecision(False, tuple(reasons))

    return PolicyDecision(True, ("policy allow",))

