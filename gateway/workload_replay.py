from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gateway.models import ModelPolicy
from gateway.policy import evaluate_policy
from gateway.rate_limit import FixedWindowRateLimiter, FixedWindowTokenBudgetLimiter
from gateway.registry import MODEL_POLICIES, PRINCIPALS
from gateway.token_budget import estimate_input_tokens


REQUIRED_OUTCOMES = (
    "allowed",
    "policy_denied",
    "rate_limited",
    "token_budget_limited",
)


@dataclass(frozen=True)
class ReplayRequest:
    principal_id: str
    model_id: str
    input_character_count: int
    reason_present: bool = True

    def estimated_input_tokens(self) -> int:
        return estimate_input_tokens("x" * self.input_character_count)


@dataclass(frozen=True)
class ReplayThresholds:
    max_allowed_p95_latency_ms: float = 120.0
    required_outcomes: tuple[str, ...] = REQUIRED_OUTCOMES

    def validate(self) -> None:
        if self.max_allowed_p95_latency_ms <= 0:
            raise ValueError("max_allowed_p95_latency_ms must be positive")
        if not self.required_outcomes:
            raise ValueError("required_outcomes must not be empty")


DEFAULT_REPLAY_REQUESTS = (
    ReplayRequest("analyst-1", "mission-summarizer", 72),
    ReplayRequest("security-1", "threat-triage", 72),
    ReplayRequest("analyst-1", "benchmark-echo", 32, reason_present=False),
    ReplayRequest("analyst-1", "threat-triage", 64),
    ReplayRequest("admin-1", "mission-summarizer", 64, reason_present=False),
    ReplayRequest("unknown-reviewer", "benchmark-echo", 32, reason_present=False),
    *(ReplayRequest("admin-1", "mission-summarizer", 4000) for _ in range(9)),
    *(ReplayRequest("admin-1", "threat-triage", 16) for _ in range(21)),
)


def build_workload_report(
    requests: tuple[ReplayRequest, ...] = DEFAULT_REPLAY_REQUESTS,
    *,
    policies: dict[str, ModelPolicy] = MODEL_POLICIES,
    thresholds: ReplayThresholds = ReplayThresholds(),
) -> dict[str, Any]:
    thresholds.validate()
    request_limiter = FixedWindowRateLimiter()
    token_budget_limiter = FixedWindowTokenBudgetLimiter()
    outcomes: Counter[str] = Counter()
    model_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    model_input_tokens: Counter[str] = Counter()
    model_allowed_latency: dict[str, list[float]] = defaultdict(list)
    allowed_latencies: list[float] = []

    for sequence, request in enumerate(requests, start=1):
        policy = policies.get(request.model_id)
        principal = PRINCIPALS.get(request.principal_id)
        token_cost = request.estimated_input_tokens()
        reason = "synthetic workload-readiness replay" if request.reason_present else None
        decision = evaluate_policy(principal, policy, reason)
        outcome = "allowed"

        if not decision.allowed:
            outcome = "policy_denied"
        elif policy is None or principal is None:
            outcome = "policy_denied"
        elif not request_limiter.allow(
            principal.principal_id,
            request.model_id,
            policy.requests_per_minute,
        ):
            outcome = "rate_limited"
        elif not token_budget_limiter.allow(
            principal.principal_id,
            request.model_id,
            token_cost,
            policy.input_tokens_per_minute,
        ):
            outcome = "token_budget_limited"

        outcomes[outcome] += 1
        model_outcomes[request.model_id][outcome] += 1
        model_input_tokens[request.model_id] += token_cost

        if outcome == "allowed":
            latency_ms = estimate_latency_ms(request.model_id, token_cost, sequence)
            allowed_latencies.append(latency_ms)
            model_allowed_latency[request.model_id].append(latency_ms)

    gates = build_release_gates(outcomes, allowed_latencies, thresholds)
    readiness_status = "pass" if all(gate["status"] == "pass" for gate in gates) else "hold"
    model_reports = [
        build_model_report(
            model_id,
            policies.get(model_id),
            model_outcomes[model_id],
            model_input_tokens[model_id],
            model_allowed_latency[model_id],
        )
        for model_id in sorted(model_outcomes)
    ]

    return {
        "schema_version": 1,
        "generated_by": "gateway.workload_replay",
        "scenario_name": "synthetic_gateway_workload_readiness",
        "data_scope": (
            "synthetic aggregate workload replay; excludes request bodies, decoded "
            "text, identities, secrets, access reasons, and production logs"
        ),
        "readiness_status": readiness_status,
        "rollout_recommendation": (
            "ready_for_local_review" if readiness_status == "pass" else "hold_for_investigation"
        ),
        "thresholds": {
            "max_allowed_p95_latency_ms": thresholds.max_allowed_p95_latency_ms,
            "required_outcomes": list(thresholds.required_outcomes),
        },
        "request_count": sum(outcomes.values()),
        "outcomes": dict(sorted(outcomes.items())),
        "latency_ms": latency_summary(allowed_latencies),
        "guardrail_coverage": {
            "observed_outcomes": sorted(outcomes),
            "missing_outcomes": [
                outcome
                for outcome in thresholds.required_outcomes
                if outcomes.get(outcome, 0) == 0
            ],
        },
        "models": model_reports,
        "release_gates": gates,
    }


def build_release_gates(
    outcomes: Counter[str],
    allowed_latencies: list[float],
    thresholds: ReplayThresholds,
) -> list[dict[str, object]]:
    missing_outcomes = [
        outcome for outcome in thresholds.required_outcomes if outcomes.get(outcome, 0) == 0
    ]
    p95_latency_ms = percentile(allowed_latencies, 95)
    return [
        {
            "name": "guardrail_outcome_coverage",
            "status": "pass" if not missing_outcomes else "hold",
            "missing_outcomes": missing_outcomes,
        },
        {
            "name": "allowed_latency_p95",
            "status": (
                "pass"
                if p95_latency_ms is not None
                and p95_latency_ms <= thresholds.max_allowed_p95_latency_ms
                else "hold"
            ),
            "observed_ms": p95_latency_ms,
            "threshold_ms": thresholds.max_allowed_p95_latency_ms,
        },
        {
            "name": "public_safe_artifact",
            "status": "pass",
            "omitted_categories": [
                "payload contents",
                "model responses",
                "identity fields",
                "access justification text",
                "auth material",
                "production logs",
            ],
        },
    ]


def build_model_report(
    model_id: str,
    policy: ModelPolicy | None,
    outcomes: Counter[str],
    total_input_tokens: int,
    allowed_latencies: list[float],
) -> dict[str, Any]:
    report = {
        "model_id": model_id,
        "request_count": sum(outcomes.values()),
        "outcomes": dict(sorted(outcomes.items())),
        "estimated_input_tokens": total_input_tokens,
        "allowed_latency_ms": latency_summary(allowed_latencies),
    }
    if policy is not None:
        report["policy_limits"] = {
            "requests_per_minute": policy.requests_per_minute,
            "input_tokens_per_minute": policy.input_tokens_per_minute,
        }
    return report


def estimate_latency_ms(model_id: str, token_cost: int, sequence: int) -> float:
    base_latency = {
        "benchmark-echo": 18.0,
        "mission-summarizer": 42.0,
        "threat-triage": 55.0,
    }.get(model_id, 50.0)
    token_component = min(token_cost * 0.018, 34.0)
    sequence_jitter = (sequence % 5) * 0.7
    return round(base_latency + token_component + sequence_jitter, 3)


def latency_summary(samples: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(samples),
        "p50": percentile(samples, 50),
        "p95": percentile(samples, 95),
        "max": round(max(samples), 3) if samples else None,
    }


def percentile(samples: list[float], percentile_value: int) -> float | None:
    if not samples:
        return None
    if not 0 < percentile_value <= 100:
        raise ValueError("percentile_value must be in (0, 100]")
    sorted_samples = sorted(samples)
    index = math.ceil((percentile_value / 100) * len(sorted_samples)) - 1
    return round(sorted_samples[max(0, index)], 3)


def write_workload_report(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_workload_report(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a public-safe synthetic gateway workload-readiness artifact."
    )
    parser.add_argument(
        "--output",
        default="artifacts/workload-readiness-evidence.json",
        help="Path for the generated JSON report.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    write_workload_report(Path(args.output))


if __name__ == "__main__":
    main()
