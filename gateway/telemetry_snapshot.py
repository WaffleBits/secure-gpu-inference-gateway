"""Build an aggregate-only snapshot from gateway metrics and probe evidence."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.request import urlopen

METRIC_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|NaN|[+-]?Inf)"
    r"(?:\s+\d+)?$"
)
ALLOWED_METRICS = {
    "security_gateway_requests_total",
    "security_gateway_input_tokens_total",
    "security_gateway_inference_latency_seconds_bucket",
    "security_gateway_inference_latency_seconds_count",
    "security_gateway_inference_latency_seconds_sum",
}
FORBIDDEN_TERMS = (
    "authorization",
    "api_key",
    "endpoint",
    "prompt",
    "principal",
    "request_body",
    "decoded_output",
)


def parse_prometheus(text: str) -> list[dict[str, Any]]:
    """Parse the small Prometheus subset emitted by this project."""
    samples: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = METRIC_LINE.match(line)
        if not match:
            raise ValueError(f"invalid Prometheus sample on line {line_number}")
        name = match.group("name")
        if name not in ALLOWED_METRICS:
            continue
        samples.append(
            {
                "name": name,
                "labels": parse_labels(match.group("labels") or ""),
                "value": parse_number(match.group("value")),
            }
        )
    return samples


def parse_labels(text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    index = 0
    while index < len(text):
        while index < len(text) and text[index] in " ,":
            index += 1
        key_start = index
        while index < len(text) and text[index] not in "=,":
            index += 1
        key = text[key_start:index].strip()
        if not key or index >= len(text) or text[index] != "=":
            raise ValueError("invalid Prometheus label")
        index += 1
        if index >= len(text) or text[index] != '"':
            raise ValueError("Prometheus labels must be quoted")
        index += 1
        value: list[str] = []
        while index < len(text):
            char = text[index]
            if char == '"':
                index += 1
                break
            if char == "\\":
                index += 1
                if index >= len(text):
                    raise ValueError("unterminated Prometheus label escape")
                escaped = text[index]
                value.append({"n": "\n", "r": "\r", "t": "\t"}.get(escaped, escaped))
            else:
                value.append(char)
            index += 1
        else:
            raise ValueError("unterminated Prometheus label")
        labels[key] = "".join(value)
        if index < len(text) and text[index] != ",":
            raise ValueError("invalid Prometheus label separator")
        index += 1
    return labels


def parse_number(value: str) -> float:
    if value in {"Inf", "+Inf"}:
        return math.inf
    if value == "-Inf":
        return -math.inf
    return float(value)


def build_snapshot(metrics_text: str, probe_report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Correlate safe gateway counters with an optional aggregate probe report."""
    samples = parse_prometheus(metrics_text)
    request_counts: defaultdict[str, int] = defaultdict(int)
    token_counts: defaultdict[str, int] = defaultdict(int)
    latency: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {"buckets": [], "count": 0, "sum_seconds": 0.0}
    )

    for sample in samples:
        labels = sample["labels"]
        name = sample["name"]
        value = sample["value"]
        if name == "security_gateway_requests_total":
            outcome = labels.get("outcome")
            if outcome:
                request_counts[outcome] += as_int(value)
        elif name == "security_gateway_input_tokens_total":
            outcome = labels.get("outcome")
            if outcome:
                token_counts[outcome] += as_int(value)
        elif name == "security_gateway_inference_latency_seconds_bucket":
            model_id = labels.get("model_id")
            bucket = labels.get("le")
            if model_id and bucket:
                latency[model_id]["buckets"].append((parse_number(bucket), as_int(value)))
        elif name == "security_gateway_inference_latency_seconds_count":
            model_id = labels.get("model_id")
            if model_id:
                latency[model_id]["count"] = as_int(value)
        elif name == "security_gateway_inference_latency_seconds_sum":
            model_id = labels.get("model_id")
            if model_id:
                latency[model_id]["sum_seconds"] = value

    total_requests = sum(request_counts.values())
    allowed_requests = request_counts.get("allowed", 0)
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "status": "pass" if total_requests > 0 and latency else "hold",
        "evidence_scope": [
            "aggregate gateway Prometheus snapshot",
            "request-outcome and estimated-token correlation",
            "histogram upper-bound latency summaries",
        ],
        "gateway": {
            "requests": {
                "total": total_requests,
                "by_outcome": dict(sorted(request_counts.items())),
                "allowed_rate": round(allowed_requests / total_requests, 4)
                if total_requests
                else None,
            },
            "estimated_input_tokens": dict(sorted(token_counts.items())),
            "latency_ms": {
                model_id: latency_summary(values)
                for model_id, values in sorted(latency.items())
            },
        },
        "release_gates": [
            {
                "name": "gateway_request_metrics_present",
                "status": "pass" if total_requests > 0 else "hold",
                "observed": total_requests,
            },
            {
                "name": "gateway_latency_metrics_present",
                "status": "pass" if latency else "hold",
                "observed_models": len(latency),
            },
        ],
        "exclusions": [
            "request bodies",
            "decoded output",
            "API keys",
            "endpoint URLs",
            "principal identities",
        ],
    }
    if probe_report is not None:
        snapshot["backend_probe"] = aggregate_probe(probe_report)
        snapshot["release_gates"].append(
            {
                "name": "backend_probe_status",
                "status": probe_report.get("status", "hold"),
                "observed_success_rate": probe_report.get("success_rate"),
            }
        )
        if probe_report.get("status") != "pass":
            snapshot["status"] = "hold"
    assert_public_safe(snapshot)
    return snapshot


def as_int(value: float) -> int:
    if not math.isfinite(value) or value < 0 or not value.is_integer():
        raise ValueError("counter values must be non-negative integers")
    return int(value)


def latency_summary(values: dict[str, Any]) -> dict[str, float | int | None]:
    count = values["count"]
    buckets = sorted(values["buckets"], key=lambda item: item[0])
    p95_upper_bound = None
    if count and buckets:
        target = max(1, math.ceil(count * 0.95))
        for upper_bound, cumulative_count in buckets:
            if math.isinf(upper_bound) or cumulative_count >= target:
                p95_upper_bound = (
                    None if math.isinf(upper_bound) else round(upper_bound * 1000, 3)
                )
                break
    return {
        "count": count,
        "mean_ms": round(values["sum_seconds"] * 1000 / count, 3) if count else None,
        "p95_upper_bound_ms": p95_upper_bound,
    }


def aggregate_probe(report: dict[str, Any]) -> dict[str, Any]:
    aggregate: dict[str, Any] = {}
    for key in (
        "status",
        "backend_protocol",
        "model_id",
        "attempted_requests",
        "successful_requests",
        "failed_requests",
        "success_rate",
        "total_tokens_reported",
    ):
        value = report.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            aggregate[key] = value
    raw_latency = report.get("latency_ms")
    if isinstance(raw_latency, dict):
        latency: dict[str, float] = {}
        for key in ("p50", "p95", "p99", "max"):
            value = raw_latency.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                latency[key] = round(float(value), 3)
        if latency:
            aggregate["latency_ms"] = latency
    aggregate["evidence_scope"] = [
        "whitelisted aggregate backend-probe fields",
        "response-shape validation and latency summary",
    ]
    return aggregate


def assert_public_safe(value: Any) -> None:
    def walk(current: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(current, dict):
            for key, child in current.items():
                normalized_key = str(key).lower()
                for term in FORBIDDEN_TERMS:
                    if term in normalized_key:
                        raise ValueError(f"public snapshot contains forbidden field: {key}")
                walk(child, (*path, normalized_key))
        elif isinstance(current, list):
            for child in current:
                walk(child, path)
        elif isinstance(current, str) and "exclusions" not in path:
            normalized_value = current.lower()
            for term in FORBIDDEN_TERMS:
                if term in normalized_value:
                    raise ValueError(f"public snapshot contains forbidden value: {term}")

    walk(value)


def fetch_metrics(url: str, timeout_seconds: float) -> str:
    with urlopen(url, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics-url", default=os.getenv("GATEWAY_METRICS_URL", ""))
    parser.add_argument("--metrics-file", type=Path)
    parser.add_argument("--probe-report", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-ms", type=float, default=5000)
    parser.add_argument("--fail-on-hold", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if bool(args.metrics_url) == bool(args.metrics_file):
        raise SystemExit("provide exactly one of --metrics-url or --metrics-file")
    if args.timeout_ms <= 0:
        raise SystemExit("--timeout-ms must be positive")
    metrics_text = (
        args.metrics_file.read_text(encoding="utf-8")
        if args.metrics_file
        else fetch_metrics(args.metrics_url, args.timeout_ms / 1000)
    )
    probe_report = (
        json.loads(args.probe_report.read_text(encoding="utf-8"))
        if args.probe_report
        else None
    )
    snapshot = build_snapshot(metrics_text, probe_report)
    rendered = json.dumps(snapshot, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if snapshot["status"] == "pass" or not args.fail_on_hold else 2


if __name__ == "__main__":
    raise SystemExit(main())
