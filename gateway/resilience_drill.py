from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gateway.deployment_readiness import build_deployment_readiness_report
from gateway.workload_replay import build_workload_report


REQUIRED_SCENARIOS = (
    "backend_latency_spike",
    "backend_error_burst",
    "queue_saturation",
    "audit_sink_backpressure",
)


@dataclass(frozen=True)
class ResilienceProbe:
    name: str
    scenario: str
    affected_model: str
    injected_condition: str
    detection_signal: str
    mitigation_path: str
    rollback_action: str
    baseline_p95_latency_ms: float
    observed_p95_latency_ms: float
    observed_error_rate: float
    observed_queue_depth: int
    recovery_seconds: float

    def validate(self) -> None:
        if not self.name:
            raise ValueError("probe name must not be empty")
        if not self.scenario:
            raise ValueError("probe scenario must not be empty")
        if not self.affected_model:
            raise ValueError("affected_model must not be empty")
        if not self.injected_condition:
            raise ValueError("injected_condition must not be empty")
        if not self.detection_signal:
            raise ValueError("detection_signal must not be empty")
        if not self.mitigation_path:
            raise ValueError("mitigation_path must not be empty")
        if not self.rollback_action:
            raise ValueError("rollback_action must not be empty")
        if self.baseline_p95_latency_ms <= 0:
            raise ValueError("baseline_p95_latency_ms must be positive")
        if self.observed_p95_latency_ms <= 0:
            raise ValueError("observed_p95_latency_ms must be positive")
        if not 0 <= self.observed_error_rate <= 1:
            raise ValueError("observed_error_rate must be in [0, 1]")
        if self.observed_queue_depth < 0:
            raise ValueError("observed_queue_depth must be non-negative")
        if self.recovery_seconds <= 0:
            raise ValueError("recovery_seconds must be positive")


@dataclass(frozen=True)
class ResilienceThresholds:
    max_latency_multiplier: float = 1.50
    max_error_rate: float = 0.02
    max_queue_depth: int = 64
    max_recovery_seconds: float = 60.0
    required_scenarios: tuple[str, ...] = REQUIRED_SCENARIOS

    def validate(self) -> None:
        if self.max_latency_multiplier <= 1:
            raise ValueError("max_latency_multiplier must be greater than 1")
        if not 0 <= self.max_error_rate <= 1:
            raise ValueError("max_error_rate must be in [0, 1]")
        if self.max_queue_depth <= 0:
            raise ValueError("max_queue_depth must be positive")
        if self.max_recovery_seconds <= 0:
            raise ValueError("max_recovery_seconds must be positive")
        if not self.required_scenarios:
            raise ValueError("required_scenarios must not be empty")


DEFAULT_RESILIENCE_PROBES = (
    ResilienceProbe(
        name="latency_spike_shadow_probe",
        scenario="backend_latency_spike",
        affected_model="mission-summarizer",
        injected_condition="synthetic backend p95 latency increase during shadow validation",
        detection_signal="p95 latency gate moves above baseline while guardrail coverage remains complete",
        mitigation_path="hold the serving-path change and keep prior route active",
        rollback_action="restore prior model route and attach the workload-readiness artifact",
        baseline_p95_latency_ms=410.0,
        observed_p95_latency_ms=488.0,
        observed_error_rate=0.0,
        observed_queue_depth=17,
        recovery_seconds=28.0,
    ),
    ResilienceProbe(
        name="error_burst_canary_probe",
        scenario="backend_error_burst",
        affected_model="threat-triage",
        injected_condition="synthetic transient backend error burst during canary review",
        detection_signal="error-rate gate trips before full rollout is allowed",
        mitigation_path="freeze canary expansion and compare against the last passing deployment evidence",
        rollback_action="disable candidate backend target and restore prior limiter descriptors",
        baseline_p95_latency_ms=530.0,
        observed_p95_latency_ms=545.0,
        observed_error_rate=0.015,
        observed_queue_depth=12,
        recovery_seconds=35.0,
    ),
    ResilienceProbe(
        name="queue_saturation_staged_probe",
        scenario="queue_saturation",
        affected_model="mission-summarizer",
        injected_condition="synthetic queue-depth pressure at staged rollout load",
        detection_signal="queue depth approaches the drill limit before request and token budgets regress",
        mitigation_path="pause staged rollout and reduce traffic fraction",
        rollback_action="return traffic to the prior capacity reservation",
        baseline_p95_latency_ms=410.0,
        observed_p95_latency_ms=585.0,
        observed_error_rate=0.005,
        observed_queue_depth=48,
        recovery_seconds=42.0,
    ),
    ResilienceProbe(
        name="audit_backpressure_probe",
        scenario="audit_sink_backpressure",
        affected_model="benchmark-echo",
        injected_condition="synthetic audit sink delay while health traffic remains allowed",
        detection_signal="audit durability warning appears before inference latency breaches the drill limit",
        mitigation_path="hold policy changes until audit sink latency returns to baseline",
        rollback_action="restore prior audit sink configuration and keep sanitized trace evidence attached",
        baseline_p95_latency_ms=95.0,
        observed_p95_latency_ms=125.0,
        observed_error_rate=0.0,
        observed_queue_depth=8,
        recovery_seconds=18.0,
    ),
)


def build_resilience_drill_report(
    probes: tuple[ResilienceProbe, ...] = DEFAULT_RESILIENCE_PROBES,
    *,
    thresholds: ResilienceThresholds = ResilienceThresholds(),
    workload_report: dict[str, Any] | None = None,
    deployment_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    thresholds.validate()
    if not probes:
        raise ValueError("probes must not be empty")
    for probe in probes:
        probe.validate()

    workload_report = workload_report or build_workload_report()
    deployment_report = deployment_report or build_deployment_readiness_report(
        workload_report=workload_report,
    )

    probe_reports = [
        build_probe_report(probe, thresholds)
        for probe in probes
    ]
    source_evidence = build_source_evidence(workload_report, deployment_report)
    release_gates = build_release_gates(probe_reports, source_evidence, thresholds)
    readiness_status = (
        "pass" if all(gate["status"] == "pass" for gate in release_gates) else "hold"
    )

    return {
        "schema_version": 1,
        "generated_by": "gateway.resilience_drill",
        "scenario_name": "synthetic_inference_gateway_resilience_drill",
        "data_scope": (
            "synthetic aggregate resilience drill; excludes request bodies, decoded text, "
            "identities, secrets, access reasons, and production logs"
        ),
        "readiness_status": readiness_status,
        "rollout_recommendation": (
            "ready_for_resilience_review"
            if readiness_status == "pass"
            else "hold_for_resilience_investigation"
        ),
        "thresholds": {
            "max_latency_multiplier": thresholds.max_latency_multiplier,
            "max_error_rate": thresholds.max_error_rate,
            "max_queue_depth": thresholds.max_queue_depth,
            "max_recovery_seconds": thresholds.max_recovery_seconds,
            "required_scenarios": list(thresholds.required_scenarios),
        },
        "source_evidence": source_evidence,
        "scenario_coverage": build_scenario_coverage(probe_reports, thresholds),
        "probes": probe_reports,
        "release_gates": release_gates,
    }


def build_probe_report(
    probe: ResilienceProbe,
    thresholds: ResilienceThresholds,
) -> dict[str, Any]:
    latency_limit_ms = probe.baseline_p95_latency_ms * thresholds.max_latency_multiplier
    checks = {
        "latency_within_multiplier": probe.observed_p95_latency_ms <= latency_limit_ms,
        "error_rate_within_limit": probe.observed_error_rate <= thresholds.max_error_rate,
        "queue_depth_within_limit": probe.observed_queue_depth <= thresholds.max_queue_depth,
        "recovery_within_limit": probe.recovery_seconds <= thresholds.max_recovery_seconds,
        "mitigation_path_defined": bool(probe.mitigation_path and probe.rollback_action),
    }
    status = "pass" if all(checks.values()) else "hold"
    return {
        "name": probe.name,
        "scenario": probe.scenario,
        "affected_model": probe.affected_model,
        "injected_condition": probe.injected_condition,
        "status": status,
        "baseline_p95_latency_ms": round(probe.baseline_p95_latency_ms, 3),
        "observed_p95_latency_ms": round(probe.observed_p95_latency_ms, 3),
        "latency_limit_ms": round(latency_limit_ms, 3),
        "observed_error_rate": round(probe.observed_error_rate, 6),
        "observed_queue_depth": probe.observed_queue_depth,
        "recovery_seconds": round(probe.recovery_seconds, 3),
        "detection_signal": probe.detection_signal,
        "mitigation_path": probe.mitigation_path,
        "rollback_action": probe.rollback_action,
        "checks": checks,
    }


def build_source_evidence(
    workload_report: dict[str, Any],
    deployment_report: dict[str, Any],
) -> dict[str, Any]:
    source_evidence = deployment_report.get("source_evidence", {})
    return {
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
        "deployment_readiness": {
            "artifact": "artifacts/deployment-readiness-evidence.json",
            "status": (
                "pass"
                if deployment_report.get("readiness_status") == "pass"
                else "hold"
            ),
            "phase_count": len(deployment_report.get("phases", [])),
        },
        "capacity_plan": source_evidence.get(
            "capacity_plan",
            {
                "artifact": "artifacts/capacity-plan-evidence.json",
                "status": "hold",
                "model_count": 0,
            },
        ),
        "distributed_limiter": source_evidence.get(
            "distributed_limiter",
            {
                "artifact": "artifacts/distributed-limiter-evidence.json",
                "status": "hold",
                "rule_count": 0,
            },
        ),
    }


def build_scenario_coverage(
    probe_reports: list[dict[str, Any]],
    thresholds: ResilienceThresholds,
) -> dict[str, Any]:
    observed_scenarios = sorted({probe["scenario"] for probe in probe_reports})
    missing_scenarios = [
        scenario
        for scenario in thresholds.required_scenarios
        if scenario not in observed_scenarios
    ]
    return {
        "observed_scenarios": observed_scenarios,
        "missing_scenarios": missing_scenarios,
    }


def build_release_gates(
    probe_reports: list[dict[str, Any]],
    source_evidence: dict[str, Any],
    thresholds: ResilienceThresholds,
) -> list[dict[str, Any]]:
    coverage = build_scenario_coverage(probe_reports, thresholds)
    held_probes = [
        probe["name"]
        for probe in probe_reports
        if probe.get("status") != "pass"
    ]
    missing_mitigations = [
        probe["name"]
        for probe in probe_reports
        if not probe.get("mitigation_path") or not probe.get("rollback_action")
    ]
    source_gates = [
        {
            "name": "workload_readiness_passed",
            "status": source_evidence["workload_readiness"]["status"],
            "artifact": source_evidence["workload_readiness"]["artifact"],
        },
        {
            "name": "deployment_readiness_passed",
            "status": source_evidence["deployment_readiness"]["status"],
            "artifact": source_evidence["deployment_readiness"]["artifact"],
        },
        {
            "name": "capacity_plan_passed",
            "status": source_evidence["capacity_plan"]["status"],
            "artifact": source_evidence["capacity_plan"]["artifact"],
        },
        {
            "name": "distributed_limiter_evidence_passed",
            "status": source_evidence["distributed_limiter"]["status"],
            "artifact": source_evidence["distributed_limiter"]["artifact"],
        },
    ]
    return [
        *source_gates,
        {
            "name": "resilience_scenario_coverage",
            "status": "pass" if not coverage["missing_scenarios"] else "hold",
            "observed_scenarios": coverage["observed_scenarios"],
            "missing_scenarios": coverage["missing_scenarios"],
        },
        {
            "name": "resilience_probe_thresholds",
            "status": "pass" if not held_probes else "hold",
            "held_probes": held_probes,
        },
        {
            "name": "mitigation_paths_defined",
            "status": "pass" if not missing_mitigations else "hold",
            "missing_mitigations": missing_mitigations,
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


def write_resilience_drill_report(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_resilience_drill_report(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write public-safe inference resilience drill evidence."
    )
    parser.add_argument(
        "--output",
        default="artifacts/resilience-drill-evidence.json",
        help="Path for the generated JSON report.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    write_resilience_drill_report(Path(args.output))


if __name__ == "__main__":
    main()
