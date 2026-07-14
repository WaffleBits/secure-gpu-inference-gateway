"""Aggregate probe harness for OpenAI-compatible inference backends."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from gateway.backend_adapter import (
    DEFAULT_TIMEOUT_SECONDS,
    BackendAdapterError,
    run_openai_compatible_inference,
)

DEFAULT_INPUT = "Return a short readiness response."
MAX_REQUESTS = 32


def percentile(values: Sequence[float], percentile_value: float) -> float | None:
    """Return a nearest-rank percentile without importing a statistics package."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, (len(ordered) * percentile_value + 99) // 100)
    return round(ordered[int(rank) - 1], 3)


def run_probe(
    model_id: str,
    inputs: Sequence[str],
    *,
    endpoint: str,
    api_key: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    request_fn: Callable[..., dict[str, object]] = run_openai_compatible_inference,
) -> dict[str, Any]:
    """Probe an endpoint and return aggregate-only readiness evidence."""
    if not model_id.strip():
        raise ValueError("model_id must not be empty")
    if not inputs or any(not value.strip() for value in inputs):
        raise ValueError("inputs must contain at least one non-empty value")
    if len(inputs) > MAX_REQUESTS:
        raise ValueError(f"inputs must contain no more than {MAX_REQUESTS} values")

    latencies: list[float] = []
    total_tokens = 0
    errors = 0
    for user_input in inputs:
        try:
            result = request_fn(
                model_id,
                user_input,
                endpoint=endpoint,
                api_key=api_key,
                timeout_seconds=timeout_seconds,
            )
        except BackendAdapterError:
            errors += 1
            continue

        latency_ms = result.get("latency_ms")
        if not isinstance(latency_ms, (int, float)) or isinstance(latency_ms, bool):
            errors += 1
            continue
        latencies.append(float(latency_ms))
        usage = result.get("usage")
        if isinstance(usage, dict):
            value = usage.get("total_tokens")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                total_tokens += value

    attempted = len(inputs)
    successful = len(latencies)
    status = "pass" if successful == attempted and successful > 0 else "hold"
    return {
        "schema_version": 1,
        "status": status,
        "backend_protocol": "openai-compatible-completions",
        "model_id": model_id,
        "attempted_requests": attempted,
        "successful_requests": successful,
        "failed_requests": errors,
        "success_rate": round(successful / attempted, 4),
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "max": round(max(latencies), 3) if latencies else None,
        },
        "total_tokens_reported": total_tokens,
        "evidence_scope": [
            "protocol and response-shape validation",
            "bounded sequential request probe",
            "aggregate latency and success-rate measurements",
        ],
        "exclusions": [
            "request bodies",
            "decoded output",
            "API keys",
            "endpoint URLs",
            "principal identities",
        ],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.getenv("INFERENCE_BACKEND_COMPLETIONS_URL", ""))
    parser.add_argument("--model", default=os.getenv("INFERENCE_BACKEND_MODEL", "readiness-model"))
    parser.add_argument("--input", action="append", dest="inputs", default=[])
    parser.add_argument("--requests", type=int, default=3)
    parser.add_argument("--timeout-ms", type=float, default=5000)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-hold", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.endpoint:
        raise SystemExit("--endpoint or INFERENCE_BACKEND_COMPLETIONS_URL is required")
    if args.requests < 1 or args.requests > MAX_REQUESTS:
        raise SystemExit(f"--requests must be between 1 and {MAX_REQUESTS}")
    inputs = args.inputs or [DEFAULT_INPUT]
    if not args.inputs:
        inputs = inputs * args.requests
    report = run_probe(
        args.model,
        inputs,
        endpoint=args.endpoint,
        api_key=os.getenv("INFERENCE_BACKEND_API_KEY"),
        timeout_seconds=args.timeout_ms / 1000,
    )
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["status"] == "pass" or not args.fail_on_hold else 2


if __name__ == "__main__":
    raise SystemExit(main())
