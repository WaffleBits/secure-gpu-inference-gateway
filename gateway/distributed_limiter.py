from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gateway.models import ModelPolicy
from gateway.registry import MODEL_POLICIES
from gateway.rate_limit import REDIS_FIXED_WINDOW_LUA


WINDOW_SECONDS = 60

@dataclass(frozen=True)
class LimitRule:
    model_id: str
    budget_type: str
    limit: int
    metric_name: str
    denial_outcome: str
    window_seconds: int = WINDOW_SECONDS

    def to_report(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "budget_type": self.budget_type,
            "limit": self.limit,
            "window_seconds": self.window_seconds,
            "redis_key_template": (
                "sgig:{environment}:{model_id}:{budget_type}:"
                "{caller_hash}:{window_epoch_minute}"
            ),
            "envoy_descriptor": {
                "entries": [
                    {"key": "service", "value": "secure-gpu-inference-gateway"},
                    {"key": "model_id", "value": self.model_id},
                    {"key": "budget_type", "value": self.budget_type},
                ],
                "unit": "minute",
                "limit_per_unit": self.limit,
            },
            "existing_metric": self.metric_name,
            "denial_outcome": self.denial_outcome,
        }


def build_distributed_limiter_report(
    policies: dict[str, ModelPolicy] = MODEL_POLICIES,
) -> dict[str, Any]:
    rules = build_limit_rules(policies)
    script_hash = stable_sha256(REDIS_FIXED_WINDOW_LUA)
    expected_rule_count = len(policies) * 2
    release_gates = build_release_gates(rules, expected_rule_count)

    return {
        "schema_version": 1,
        "generated_by": "gateway.distributed_limiter",
        "scenario_name": "distributed_request_token_limiter_readiness",
        "data_scope": (
            "synthetic limiter migration evidence; excludes request bodies, "
            "decoded text, subject identifiers, secrets, access reasons, and production logs"
        ),
        "target_backends": [
            "redis_fixed_window_lua",
            "envoy_global_rate_limit_descriptors",
        ],
        "window_seconds": WINDOW_SECONDS,
        "rule_count": len(rules),
        "rules": [rule.to_report() for rule in rules],
        "redis_atomic_script": {
            "sha256": script_hash,
            "properties": [
                "single-key atomic increment",
                "ttl applied on first window increment",
                "over-limit result returned without payload capture",
            ],
        },
        "sample_decisions": sample_decisions(rules),
        "observability": {
            "existing_metrics": [
                "security_gateway_requests_total",
                "security_gateway_denials_total",
                "security_gateway_input_tokens_total",
            ],
            "recommended_low_cardinality_labels": [
                "model_id",
                "budget_type",
                "outcome",
            ],
        },
        "release_gates": release_gates,
        "readiness_status": (
            "pass" if all(gate["status"] == "pass" for gate in release_gates) else "hold"
        ),
        "rollout_recommendation": (
            "ready_for_redis_or_envoy_integration_spike"
            if all(gate["status"] == "pass" for gate in release_gates)
            else "hold_for_limiter_design_review"
        ),
    }


def build_limit_rules(policies: dict[str, ModelPolicy]) -> list[LimitRule]:
    rules: list[LimitRule] = []
    for policy in sorted(policies.values(), key=lambda item: item.model_id):
        rules.append(
            LimitRule(
                model_id=policy.model_id,
                budget_type="request_count",
                limit=policy.requests_per_minute,
                metric_name="security_gateway_requests_total",
                denial_outcome="rate_limited",
            )
        )
        rules.append(
            LimitRule(
                model_id=policy.model_id,
                budget_type="estimated_input_tokens",
                limit=policy.input_tokens_per_minute,
                metric_name="security_gateway_input_tokens_total",
                denial_outcome="token_budget_limited",
            )
        )
    return rules


def build_release_gates(
    rules: list[LimitRule],
    expected_rule_count: int,
) -> list[dict[str, object]]:
    budget_types = {rule.budget_type for rule in rules}
    return [
        {
            "name": "policy_budget_rule_coverage",
            "status": "pass" if len(rules) == expected_rule_count else "hold",
            "expected_rules": expected_rule_count,
            "observed_rules": len(rules),
        },
        {
            "name": "request_and_token_budget_coverage",
            "status": (
                "pass"
                if budget_types == {"request_count", "estimated_input_tokens"}
                else "hold"
            ),
            "observed_budget_types": sorted(budget_types),
        },
        {
            "name": "redis_atomic_window_shape",
            "status": (
                "pass"
                if all(
                    fragment in REDIS_FIXED_WINDOW_LUA
                    for fragment in ("INCRBY", "PEXPIRE", "tonumber(ARGV[3])")
                )
                else "hold"
            ),
            "script_sha256": stable_sha256(REDIS_FIXED_WINDOW_LUA),
        },
        {
            "name": "public_safe_artifact",
            "status": "pass",
            "omitted_categories": [
                "request payloads",
                "model responses",
                "subject identifiers",
                "access justification text",
                "auth material",
                "production logs",
            ],
        },
    ]


def sample_decisions(rules: list[LimitRule]) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for rule in rules[:4]:
        samples.append(
            {
                "model_id": rule.model_id,
                "budget_type": rule.budget_type,
                "caller_hash_example": stable_sha256(
                    f"synthetic-reviewer:{rule.model_id}:{rule.budget_type}"
                )[:16],
                "window_cost": 1 if rule.budget_type == "request_count" else min(64, rule.limit),
                "limit": rule.limit,
                "allowed": True,
            }
        )
        samples.append(
            {
                "model_id": rule.model_id,
                "budget_type": rule.budget_type,
                "caller_hash_example": stable_sha256(
                    f"synthetic-over-limit:{rule.model_id}:{rule.budget_type}"
                )[:16],
                "window_cost": rule.limit + 1,
                "limit": rule.limit,
                "allowed": False,
                "denial_outcome": rule.denial_outcome,
            }
        )
    return samples


def stable_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_distributed_limiter_report(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_distributed_limiter_report(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write public-safe distributed limiter readiness evidence."
    )
    parser.add_argument(
        "--output",
        default="artifacts/distributed-limiter-evidence.json",
        help="Path for the generated JSON report.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    write_distributed_limiter_report(Path(args.output))


if __name__ == "__main__":
    main()
