from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gateway.capacity_plan import build_capacity_report
from gateway.distributed_limiter import build_distributed_limiter_report
from gateway.workload_replay import build_workload_report


@dataclass(frozen=True)
class DeploymentPhase:
    name: str
    traffic_fraction: float
    capacity_reservation_fraction: float
    validation_mode: str
    rollback_trigger: str
    required_evidence: tuple[str, ...]

    def validate(self) -> None:
        if not self.name:
            raise ValueError("deployment phase name must not be empty")
        if not 0 <= self.traffic_fraction <= 1:
            raise ValueError("traffic_fraction must be in [0, 1]")
        if not 0 < self.capacity_reservation_fraction <= 1:
            raise ValueError("capacity_reservation_fraction must be in (0, 1]")
        if self.capacity_reservation_fraction < self.traffic_fraction:
            raise ValueError("capacity_reservation_fraction must cover traffic_fraction")
        if not self.validation_mode:
            raise ValueError("validation_mode must not be empty")
        if not self.rollback_trigger:
            raise ValueError("rollback_trigger must not be empty")
        if not self.required_evidence:
            raise ValueError("required_evidence must not be empty")


@dataclass(frozen=True)
class DeploymentThresholds:
    max_phase_policy_utilization: float = 0.80

    def validate(self) -> None:
        if not 0 < self.max_phase_policy_utilization <= 1:
            raise ValueError("max_phase_policy_utilization must be in (0, 1]")


DEFAULT_DEPLOYMENT_PHASES = (
    DeploymentPhase(
        name="shadow_validation",
        traffic_fraction=0.0,
        capacity_reservation_fraction=0.05,
        validation_mode="shadow",
        rollback_trigger="candidate shadow mismatches baseline or readiness gates regress",
        required_evidence=(
            "artifacts/workload-readiness-evidence.json",
            "artifacts/capacity-plan-evidence.json",
        ),
    ),
    DeploymentPhase(
        name="canary_10_percent",
        traffic_fraction=0.10,
        capacity_reservation_fraction=0.15,
        validation_mode="canary",
        rollback_trigger="p95 latency, denial rate, or limiter outcome deviates from baseline",
        required_evidence=(
            "artifacts/workload-readiness-evidence.json",
            "artifacts/distributed-limiter-evidence.json",
        ),
    ),
    DeploymentPhase(
        name="staged_rollout_50_percent",
        traffic_fraction=0.50,
        capacity_reservation_fraction=0.60,
        validation_mode="progressive_rollout",
        rollback_trigger="capacity, token budget, or guardrail coverage moves outside gates",
        required_evidence=(
            "artifacts/capacity-plan-evidence.json",
            "artifacts/distributed-limiter-evidence.json",
        ),
    ),
    DeploymentPhase(
        name="full_rollout",
        traffic_fraction=1.0,
        capacity_reservation_fraction=1.0,
        validation_mode="full_review_release",
        rollback_trigger="SLO breach, audit gap, or limiter migration error",
        required_evidence=(
            "artifacts/workload-readiness-evidence.json",
            "artifacts/capacity-plan-evidence.json",
            "artifacts/distributed-limiter-evidence.json",
        ),
    ),
)


def build_deployment_readiness_report(
    *,
    capacity_report: dict[str, Any] | None = None,
    workload_report: dict[str, Any] | None = None,
    limiter_report: dict[str, Any] | None = None,
    phases: tuple[DeploymentPhase, ...] = DEFAULT_DEPLOYMENT_PHASES,
    thresholds: DeploymentThresholds = DeploymentThresholds(),
) -> dict[str, Any]:
    thresholds.validate()
    for phase in phases:
        phase.validate()

    capacity_report = capacity_report or build_capacity_report()
    workload_report = workload_report or build_workload_report()
    limiter_report = limiter_report or build_distributed_limiter_report()

    source_status = build_source_status(capacity_report, workload_report, limiter_report)
    phase_reports = build_phase_reports(capacity_report, phases)
    release_gates = build_release_gates(
        source_status,
        phase_reports,
        phases,
        thresholds,
    )
    readiness_status = (
        "pass" if all(gate["status"] == "pass" for gate in release_gates) else "hold"
    )

    return {
        "schema_version": 1,
        "generated_by": "gateway.deployment_readiness",
        "scenario_name": "synthetic_inference_gateway_deployment_readiness",
        "data_scope": (
            "synthetic aggregate deployment review; excludes request bodies, decoded "
            "text, identities, secrets, access reasons, and production logs"
        ),
        "readiness_status": readiness_status,
        "rollout_recommendation": (
            "ready_for_local_deployment_review"
            if readiness_status == "pass"
            else "hold_for_release_review"
        ),
        "source_evidence": source_status,
        "thresholds": {
            "max_phase_policy_utilization": thresholds.max_phase_policy_utilization,
        },
        "phases": phase_reports,
        "rollback_actions": [
            "restore the prior model policy and limiter descriptors",
            "disable the candidate route or deployment target",
            "keep prior capacity, workload, and limiter artifacts attached to the release note",
        ],
        "release_gates": release_gates,
    }


def build_source_status(
    capacity_report: dict[str, Any],
    workload_report: dict[str, Any],
    limiter_report: dict[str, Any],
) -> dict[str, Any]:
    capacity_models = capacity_report.get("models", [])
    capacity_pass = bool(capacity_models) and all(
        model.get("status") == "within_synthetic_capacity"
        for model in capacity_models
    )
    return {
        "capacity_plan": {
            "artifact": "artifacts/capacity-plan-evidence.json",
            "status": "pass" if capacity_pass else "hold",
            "model_count": len(capacity_models),
        },
        "workload_readiness": {
            "artifact": "artifacts/workload-readiness-evidence.json",
            "status": (
                "pass"
                if workload_report.get("readiness_status") == "pass"
                else "hold"
            ),
            "observed_outcomes": workload_report.get("guardrail_coverage", {}).get(
                "observed_outcomes",
                [],
            ),
        },
        "distributed_limiter": {
            "artifact": "artifacts/distributed-limiter-evidence.json",
            "status": (
                "pass"
                if limiter_report.get("readiness_status") == "pass"
                else "hold"
            ),
            "rule_count": limiter_report.get("rule_count", 0),
        },
    }


def build_phase_reports(
    capacity_report: dict[str, Any],
    phases: tuple[DeploymentPhase, ...],
) -> list[dict[str, Any]]:
    capacity_models = sorted(
        capacity_report.get("models", []),
        key=lambda model: model.get("model_id", ""),
    )
    reports: list[dict[str, Any]] = []
    for phase in phases:
        model_schedules = [
            build_model_schedule(model, phase)
            for model in capacity_models
        ]
        max_policy_utilization = max(
            (
                max(
                    schedule["scheduled_policy_request_utilization"],
                    schedule["scheduled_policy_input_token_utilization"],
                )
                for schedule in model_schedules
            ),
            default=0,
        )
        reports.append(
            {
                "name": phase.name,
                "traffic_fraction": phase.traffic_fraction,
                "capacity_reservation_fraction": phase.capacity_reservation_fraction,
                "validation_mode": phase.validation_mode,
                "required_evidence": list(phase.required_evidence),
                "rollback_trigger": phase.rollback_trigger,
                "max_scheduled_policy_utilization": round(max_policy_utilization, 6),
                "models": model_schedules,
            }
        )
    return reports


def build_model_schedule(
    capacity_model: dict[str, Any],
    phase: DeploymentPhase,
) -> dict[str, Any]:
    reservation_fraction = phase.capacity_reservation_fraction
    request_utilization = capacity_model.get("policy_request_utilization", 0) * reservation_fraction
    input_token_utilization = (
        capacity_model.get("policy_input_token_utilization", 0) * reservation_fraction
    )
    return {
        "model_id": capacity_model.get("model_id"),
        "capacity_status": capacity_model.get("status"),
        "scheduled_policy_request_utilization": round(request_utilization, 6),
        "scheduled_policy_input_token_utilization": round(input_token_utilization, 6),
        "reserved_requests_per_minute": round(
            capacity_model.get("modeled_requests_per_minute", 0) * reservation_fraction,
            3,
        ),
        "reserved_input_tokens_per_minute": round(
            capacity_model.get("modeled_input_tokens_per_minute", 0) * reservation_fraction,
            3,
        ),
    }


def build_release_gates(
    source_status: dict[str, Any],
    phase_reports: list[dict[str, Any]],
    phases: tuple[DeploymentPhase, ...],
    thresholds: DeploymentThresholds,
) -> list[dict[str, Any]]:
    source_gates = [
        {
            "name": "capacity_plan_passed",
            "status": source_status["capacity_plan"]["status"],
            "artifact": source_status["capacity_plan"]["artifact"],
        },
        {
            "name": "workload_readiness_passed",
            "status": source_status["workload_readiness"]["status"],
            "artifact": source_status["workload_readiness"]["artifact"],
        },
        {
            "name": "distributed_limiter_evidence_passed",
            "status": source_status["distributed_limiter"]["status"],
            "artifact": source_status["distributed_limiter"]["artifact"],
        },
    ]
    max_scheduled_utilization = max(
        (phase["max_scheduled_policy_utilization"] for phase in phase_reports),
        default=0,
    )
    shape_ok = bool(phases) and phases[-1].traffic_fraction == 1.0
    rollback_ok = all(phase.rollback_trigger for phase in phases)

    return [
        *source_gates,
        {
            "name": "phased_rollout_shape",
            "status": "pass" if shape_ok else "hold",
            "phase_names": [phase.name for phase in phases],
        },
        {
            "name": "staged_capacity_utilization",
            "status": (
                "pass"
                if max_scheduled_utilization <= thresholds.max_phase_policy_utilization
                else "hold"
            ),
            "observed_max": round(max_scheduled_utilization, 6),
            "threshold": thresholds.max_phase_policy_utilization,
        },
        {
            "name": "rollback_path_defined",
            "status": "pass" if rollback_ok else "hold",
            "rollback_trigger_count": sum(1 for phase in phases if phase.rollback_trigger),
        },
        {
            "name": "public_safe_artifact",
            "status": "pass",
            "omitted_categories": [
                "request payloads",
                "model responses",
                "identity fields",
                "access justification text",
                "auth material",
                "production logs",
            ],
        },
    ]


def write_deployment_readiness_report(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_deployment_readiness_report(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write public-safe inference deployment readiness evidence."
    )
    parser.add_argument(
        "--output",
        default="artifacts/deployment-readiness-evidence.json",
        help="Path for the generated JSON report.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    write_deployment_readiness_report(Path(args.output))


if __name__ == "__main__":
    main()
