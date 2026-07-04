from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

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
        "ai.gateway.estimated_input_tokens",
        "ai.gateway.token_budget_limit",
        "ai.gateway.trace_source",
    }
)


class TraceExporter(Protocol):
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
        ...


class CompositeTraceExporter:
    def __init__(self, exporters: tuple[TraceExporter, ...]) -> None:
        self.exporters = exporters

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
        for exporter in self.exporters:
            exporter.write_span(
                trace,
                model_id=model_id,
                outcome=outcome,
                auth_method=auth_method,
                decision_reasons=decision_reasons,
                latency_ms=latency_ms,
                started_at_unix_nano=started_at_unix_nano,
                ended_at_unix_nano=ended_at_unix_nano,
                extra_attributes=extra_attributes,
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


class OtlpHttpTraceExporter:
    def __init__(
        self,
        endpoint: str,
        *,
        service_name: str = "secure-gpu-inference-gateway",
        timeout_seconds: float = 2.0,
    ) -> None:
        self.endpoint = endpoint
        self.service_name = service_name
        self.timeout_seconds = timeout_seconds
        self.last_error: str | None = None

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
        payload = build_otlp_traces_payload((record,))
        try:
            post_otlp_payload(
                self.endpoint,
                payload,
                timeout_seconds=self.timeout_seconds,
            )
            self.last_error = None
        except OSError as exc:
            self.last_error = str(exc)


def build_trace_exporter_from_env() -> TraceExporter | None:
    service_name = os.getenv("OTEL_SERVICE_NAME", "secure-gpu-inference-gateway")
    exporters: list[TraceExporter] = []

    path = _empty_to_none(
        os.getenv("TRACE_EXPORT_PATH") or os.getenv("OTEL_TRACE_EXPORT_PATH")
    )
    if path is not None:
        exporters.append(JsonlTraceExporter(path, service_name=service_name))

    endpoint = _empty_to_none(
        os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or os.getenv("OTLP_TRACES_ENDPOINT")
    )
    if endpoint is not None:
        exporters.append(
            OtlpHttpTraceExporter(
                endpoint,
                service_name=service_name,
                timeout_seconds=_env_float("OTEL_EXPORTER_OTLP_TIMEOUT", 2.0),
            )
        )

    if not exporters:
        return None
    if len(exporters) == 1:
        return exporters[0]
    return CompositeTraceExporter(tuple(exporters))


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


def build_otlp_traces_payload(
    records: tuple[Mapping[str, object], ...] | list[Mapping[str, object]]
) -> dict[str, object]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for record in records:
        resource = _mapping(record["resource"])
        scope = _mapping(record["scope"])
        resource_key = json.dumps(resource, sort_keys=True)
        scope_key = json.dumps(scope, sort_keys=True)
        group_key = (resource_key, scope_key)
        if group_key not in grouped:
            grouped[group_key] = {
                "resource": {"attributes": otlp_attributes(resource)},
                "scopeSpans": [
                    {
                        "scope": {
                            "name": str(scope.get("name", "")),
                            "schemaUrl": str(scope.get("schema_url", "")),
                        },
                        "spans": [],
                    }
                ],
            }
        scope_spans = grouped[group_key]["scopeSpans"]
        assert isinstance(scope_spans, list)
        scope_span = scope_spans[0]
        assert isinstance(scope_span, dict)
        spans = scope_span["spans"]
        assert isinstance(spans, list)
        spans.append(otlp_span(_mapping(record["span"])))

    return {"resourceSpans": list(grouped.values())}


def load_jsonl_span_records(path: str | Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def write_otlp_payload(
    input_path: str | Path,
    output_path: str | Path,
) -> dict[str, object]:
    payload = build_otlp_traces_payload(load_jsonl_span_records(input_path))
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def post_otlp_payload(
    endpoint: str,
    payload: Mapping[str, object],
    *,
    timeout_seconds: float = 2.0,
) -> None:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        response.read()


def otlp_span(span: Mapping[str, object]) -> dict[str, object]:
    status = _mapping(span.get("status", {}))
    return {
        "traceId": str(span["trace_id"]),
        "spanId": str(span["span_id"]),
        "parentSpanId": str(span.get("parent_span_id") or ""),
        "name": str(span["name"]),
        "kind": 2,
        "startTimeUnixNano": str(span["start_time_unix_nano"]),
        "endTimeUnixNano": str(span["end_time_unix_nano"]),
        "attributes": otlp_attributes(_mapping(span.get("attributes", {}))),
        "status": {
            "code": 1 if status.get("code") == "OK" else 2,
            "message": str(status.get("message", "")),
        },
    }


def otlp_attributes(attributes: Mapping[str, object]) -> list[dict[str, object]]:
    return [
        {"key": str(key), "value": otlp_value(value)}
        for key, value in sorted(attributes.items())
    ]


def otlp_value(value: object) -> dict[str, object]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def _empty_to_none(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()


def _env_float(name: str, default: float) -> float:
    value = _empty_to_none(os.getenv(name))
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("expected mapping-shaped span record")
    return value
