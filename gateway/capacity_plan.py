from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gateway.models import ModelPolicy
from gateway.registry import MODEL_POLICIES


DEFAULT_TARGET_GPU_UTILIZATION = 0.72
DEFAULT_SAFETY_MARGIN = 0.20


@dataclass(frozen=True)
class CapacityProfile:
    model_id: str
    profile_name: str
    measured_requests_per_second: float
    measured_input_tokens_per_second: float
    measured_decode_tokens_per_second: float
    observed_p95_latency_ms: float
    observed_gpu_utilization: float
    gpu_hourly_cost_usd: float
    accelerator_class: str = "synthetic-review-gpu"

    def validate(self) -> None:
        positive_fields = {
            "measured_requests_per_second": self.measured_requests_per_second,
            "measured_input_tokens_per_second": self.measured_input_tokens_per_second,
            "measured_decode_tokens_per_second": self.measured_decode_tokens_per_second,
            "observed_p95_latency_ms": self.observed_p95_latency_ms,
            "gpu_hourly_cost_usd": self.gpu_hourly_cost_usd,
        }
        for field_name, value in positive_fields.items():
            if value <= 0:
                raise ValueError(f"{field_name} must be positive")
        if not 0 < self.observed_gpu_utilization <= 1:
            raise ValueError("observed_gpu_utilization must be in (0, 1]")


@dataclass(frozen=True)
class CapacityAssumption:
    target_gpu_utilization: float = DEFAULT_TARGET_GPU_UTILIZATION
    safety_margin: float = DEFAULT_SAFETY_MARGIN

    def validate(self) -> None:
        if not 0 < self.target_gpu_utilization <= 1:
            raise ValueError("target_gpu_utilization must be in (0, 1]")
        if not 0 <= self.safety_margin < 1:
            raise ValueError("safety_margin must be in [0, 1)")


DEFAULT_CAPACITY_PROFILES = (
    CapacityProfile(
        model_id="mission-summarizer",
        profile_name="synthetic-standard-summary",
        measured_requests_per_second=12.5,
        measured_input_tokens_per_second=3300,
        measured_decode_tokens_per_second=820,
        observed_p95_latency_ms=410,
        observed_gpu_utilization=0.68,
        gpu_hourly_cost_usd=2.80,
    ),
    CapacityProfile(
        model_id="threat-triage",
        profile_name="synthetic-restricted-triage",
        measured_requests_per_second=9.4,
        measured_input_tokens_per_second=2800,
        measured_decode_tokens_per_second=690,
        observed_p95_latency_ms=530,
        observed_gpu_utilization=0.74,
        gpu_hourly_cost_usd=2.80,
    ),
    CapacityProfile(
        model_id="benchmark-echo",
        profile_name="synthetic-health-check",
        measured_requests_per_second=55.0,
        measured_input_tokens_per_second=9100,
        measured_decode_tokens_per_second=1300,
        observed_p95_latency_ms=95,
        observed_gpu_utilization=0.36,
        gpu_hourly_cost_usd=1.20,
    ),
)


def build_capacity_report(
    profiles: tuple[CapacityProfile, ...] = DEFAULT_CAPACITY_PROFILES,
    policies: dict[str, ModelPolicy] = MODEL_POLICIES,
    assumption: CapacityAssumption = CapacityAssumption(),
) -> dict[str, Any]:
    assumption.validate()
    estimates = [
        estimate_profile_capacity(profile, policies[profile.model_id], assumption)
        for profile in profiles
    ]
    return {
        "schema_version": 1,
        "data_scope": "synthetic aggregate benchmark profile; excludes request bodies, decoded text, identities, secrets, and production logs",
        "assumptions": {
            "target_gpu_utilization": assumption.target_gpu_utilization,
            "safety_margin": assumption.safety_margin,
            "cost_basis": "single synthetic accelerator-hour",
        },
        "models": estimates,
    }


def estimate_profile_capacity(
    profile: CapacityProfile,
    policy: ModelPolicy,
    assumption: CapacityAssumption,
) -> dict[str, Any]:
    profile.validate()
    assumption.validate()
    utilization_scale = assumption.target_gpu_utilization / profile.observed_gpu_utilization
    usable_scale = utilization_scale * (1 - assumption.safety_margin)
    modeled_requests_per_second = profile.measured_requests_per_second * usable_scale
    modeled_input_tokens_per_second = (
        profile.measured_input_tokens_per_second * usable_scale
    )
    modeled_decode_tokens_per_second = (
        profile.measured_decode_tokens_per_second * usable_scale
    )
    modeled_requests_per_minute = modeled_requests_per_second * 60
    modeled_input_tokens_per_minute = modeled_input_tokens_per_second * 60
    modeled_decode_tokens_per_minute = modeled_decode_tokens_per_second * 60
    policy_request_utilization = (
        policy.requests_per_minute / modeled_requests_per_minute
    )
    policy_input_token_utilization = (
        policy.input_tokens_per_minute / modeled_input_tokens_per_minute
    )
    limiting_ratio = max(policy_request_utilization, policy_input_token_utilization)
    status = (
        "within_synthetic_capacity"
        if limiting_ratio <= 1
        else "exceeds_synthetic_capacity"
    )

    return {
        "model_id": profile.model_id,
        "profile_name": profile.profile_name,
        "accelerator_class": profile.accelerator_class,
        "observed_p95_latency_ms": round(profile.observed_p95_latency_ms, 3),
        "observed_gpu_utilization": round(profile.observed_gpu_utilization, 6),
        "modeled_requests_per_minute": round(modeled_requests_per_minute, 3),
        "modeled_input_tokens_per_minute": round(modeled_input_tokens_per_minute, 3),
        "modeled_decode_tokens_per_minute": round(modeled_decode_tokens_per_minute, 3),
        "policy_requests_per_minute": policy.requests_per_minute,
        "policy_input_tokens_per_minute": policy.input_tokens_per_minute,
        "policy_request_utilization": round(policy_request_utilization, 6),
        "policy_input_token_utilization": round(policy_input_token_utilization, 6),
        "cost_per_1000_requests_usd": round(
            cost_per_units(profile.gpu_hourly_cost_usd, modeled_requests_per_second, 1000),
            6,
        ),
        "cost_per_1m_input_tokens_usd": round(
            cost_per_units(
                profile.gpu_hourly_cost_usd,
                modeled_input_tokens_per_second,
                1_000_000,
            ),
            6,
        ),
        "status": status,
    }


def cost_per_units(hourly_cost: float, units_per_second: float, unit_count: int) -> float:
    if hourly_cost <= 0:
        raise ValueError("hourly_cost must be positive")
    if units_per_second <= 0:
        raise ValueError("units_per_second must be positive")
    return hourly_cost / (units_per_second * 3600) * unit_count


def write_capacity_report(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = build_capacity_report()
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write a public-safe synthetic gateway capacity plan artifact."
    )
    parser.add_argument(
        "--output",
        default="artifacts/capacity-plan-evidence.json",
        help="Path for the generated JSON report.",
    )
    args = parser.parse_args()
    write_capacity_report(Path(args.output))


if __name__ == "__main__":
    main()
