from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass


_TRACEPARENT_PATTERN = re.compile(
    r"^(?P<version>[0-9a-fA-F]{2})-"
    r"(?P<trace_id>[0-9a-fA-F]{32})-"
    r"(?P<parent_id>[0-9a-fA-F]{16})-"
    r"(?P<trace_flags>[0-9a-fA-F]{2})$"
)


@dataclass(frozen=True)
class RequestTrace:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    sampled: bool
    source: str


def resolve_trace_context(
    traceparent: str | None,
    trace_id_factory: Callable[[], str] = lambda: secrets.token_hex(16),
    span_id_factory: Callable[[], str] = lambda: secrets.token_hex(8),
) -> RequestTrace:
    parsed = parse_traceparent(traceparent)
    span_id = _nonzero_hex(span_id_factory, 16)

    if parsed is None:
        return RequestTrace(
            trace_id=_nonzero_hex(trace_id_factory, 32),
            span_id=span_id,
            parent_span_id=None,
            sampled=False,
            source="generated",
        )

    return RequestTrace(
        trace_id=parsed["trace_id"],
        span_id=span_id,
        parent_span_id=parsed["parent_id"],
        sampled=bool(int(parsed["trace_flags"], 16) & 1),
        source="remote",
    )


def parse_traceparent(traceparent: str | None) -> dict[str, str] | None:
    if not traceparent:
        return None

    match = _TRACEPARENT_PATTERN.match(traceparent.strip())
    if not match:
        return None

    parts = {key: value.lower() for key, value in match.groupdict().items()}
    if parts["version"] == "ff":
        return None

    if _is_all_zero(parts["trace_id"]) or _is_all_zero(parts["parent_id"]):
        return None

    return parts


def format_traceparent(trace: RequestTrace) -> str:
    flags = "01" if trace.sampled else "00"
    return f"00-{trace.trace_id}-{trace.span_id}-{flags}"


def _nonzero_hex(factory: Callable[[], str], expected_length: int) -> str:
    value = factory().lower()
    if len(value) != expected_length or not re.fullmatch(r"[0-9a-f]+", value):
        raise ValueError("trace id factory returned invalid hex")
    if _is_all_zero(value):
        raise ValueError("trace id factory returned zero value")
    return value


def _is_all_zero(value: str) -> bool:
    return set(value) == {"0"}
