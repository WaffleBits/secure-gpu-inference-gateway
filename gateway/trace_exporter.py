from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from gateway.trace_context import RequestTrace, format_traceparent


SAFE_ATTRIBUTE_KEYS = frozenset(
    {
        "http.route",
        "ai.gateway.auth_method",
        "ai.gateway.decision_count",
        "ai.gateway.latency_ms",
        "ai.gateway.model_id",
        "ai.gateway.outcome",
        "ai.gateway.sampled",
        "ai.gateway.trace_source",
    }
)


class JsonlTraceExporter:
    def __init__(
        self,
        path: str | Path,
        *,
        service_name: str = "secure-gpu-inference-gateway",
    ) -> None:
        self.path = Path(path)
        self.service_name = service_name

    @classmethod
    def from_env(cls) -> JsonlTraceExporter | None:
        path = _empty_to_none(
            os.getenv("TRACE_EXPORT_PATH") or os.getenv("OTEL_TRACE_EXPORT_PATH")
        )
        if path is None:
            return None

        return cls(
            path,
            service_name=os.getenv(
                "OTEL_SERVICE_NAME",
                "secure-gpu-inference-gateway",
            ),
        )

    def write_span(
        self,
        trace: RequestTrace,
        *,
        model_id: str,
        outcome: str,
        auth_method: str,
        decision_reasons: tuple[str, ...],
        latency_ms: float | None,
        started_at_unix_nano: int,
        ended_at_unix_nano: int,
        extra_attributes: Mapping[str, object] | None = None,
    ) -> None:
        record = build_span_record(
            trace,
            service_name=self.service_name,
            model_id=model_id,
            outcome=outcome,
            auth_method=auth_method,
            decision_reasons=decision_reasons,
            latency_ms=latency_ms,
            started_at_unix_nano=started_at_unix_nano,
            ended_at_unix_nano=ended_at_unix_nano,
            extra_attributes=extra_attributes,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def build_span_record(
    trace: RequestTrace,
    *,
    service_name: str,
    model_id: str,
    outcome: str,
    auth_method: str,
    decision_reasons: tuple[str, ...],
    latency_ms: float | None,
    started_at_unix_nano: int,
    ended_at_unix_nano: int,
    extra_attributes: Mapping[str, object] | None = None,
) -> dict[str, object]:
    attributes: dict[str, object] = {
        "http.route": "/v1/infer/{model_id}",
        "ai.gateway.auth_method": auth_method,
        "ai.gateway.decision_count": len(decision_reasons),
        "ai.gateway.model_id": model_id,
        "ai.gateway.outcome": outcome,
        "ai.gateway.sampled": trace.sampled,
        "ai.gateway.trace_source": trace.source,
    }
    if latency_ms is not None:
        attributes["ai.gateway.latency_ms"] = round(latency_ms, 3)
    if extra_attributes:
        attributes.update(extra_attributes)

    return {
        "resource": {
            "service.name": service_name,
        },
        "scope": {
            "name": "gateway.trace_exporter",
            "schema_url": "https://opentelemetry.io/schemas/1.28.0",
        },
        "span": {
            "attributes": sanitize_attributes(attributes),
            "end_time_unix_nano": ended_at_unix_nano,
            "kind": "SERVER",
            "name": "POST /v1/infer/{model_id}",
            "parent_span_id": trace.parent_span_id,
            "span_id": trace.span_id,
            "start_time_unix_nano": started_at_unix_nano,
            "status": {
                "code": "OK" if outcome == "allowed" else "ERROR",
                "message": outcome,
            },
            "trace_id": trace.trace_id,
            "traceparent": format_traceparent(trace),
        },
    }


def sanitize_attributes(attributes: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in sorted(attributes.items())
        if key in SAFE_ATTRIBUTE_KEYS and isinstance(value, str | int | float | bool)
    }


def _empty_to_none(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()
