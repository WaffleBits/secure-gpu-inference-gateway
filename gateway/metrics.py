from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from threading import Lock

from gateway.models import ModelPolicy


DEFAULT_LATENCY_BUCKETS = (0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


@dataclass(frozen=True)
class MetricSample:
    name: str
    labels: dict[str, str]
    value: float


class GatewayMetrics:
    def __init__(self, latency_buckets: tuple[float, ...] = DEFAULT_LATENCY_BUCKETS) -> None:
        self.latency_buckets = tuple(sorted(latency_buckets))
        self._requests: Counter[tuple[str, str]] = Counter()
        self._denials: Counter[tuple[str, str]] = Counter()
        self._latency_buckets: Counter[tuple[str, float]] = Counter()
        self._latency_count: Counter[str] = Counter()
        self._latency_sum: Counter[str] = Counter()
        self._lock = Lock()

    def record_request(
        self,
        model_id: str,
        outcome: str,
        denial_reasons: tuple[str, ...] = (),
    ) -> None:
        with self._lock:
            self._requests[(model_id, outcome)] += 1
            if outcome != "allowed":
                reasons = denial_reasons or ("unspecified",)
                for reason in reasons:
                    self._denials[(model_id, reason)] += 1

    def observe_latency(self, model_id: str, latency_seconds: float) -> None:
        with self._lock:
            self._latency_count[model_id] += 1
            self._latency_sum[model_id] += latency_seconds
            for bucket in self.latency_buckets:
                if latency_seconds <= bucket:
                    self._latency_buckets[(model_id, bucket)] += 1

    def render_prometheus(self, model_policies: dict[str, ModelPolicy]) -> str:
        with self._lock:
            samples = [
                *self._model_policy_samples(model_policies),
                *self._request_samples(),
                *self._denial_samples(),
                *self._latency_samples(),
            ]

        sections = [
            "# HELP security_gateway_model_policy_info Configured model policy metadata.",
            "# TYPE security_gateway_model_policy_info gauge",
            *format_samples(
                sample
                for sample in samples
                if sample.name == "security_gateway_model_policy_info"
            ),
            "# HELP security_gateway_requests_total Inference gateway requests by model and outcome.",
            "# TYPE security_gateway_requests_total counter",
            *format_samples(
                sample
                for sample in samples
                if sample.name == "security_gateway_requests_total"
            ),
            "# HELP security_gateway_denials_total Denied requests by model and reason.",
            "# TYPE security_gateway_denials_total counter",
            *format_samples(
                sample
                for sample in samples
                if sample.name == "security_gateway_denials_total"
            ),
            "# HELP security_gateway_inference_latency_seconds Mock inference latency histogram.",
            "# TYPE security_gateway_inference_latency_seconds histogram",
            *format_samples(
                sample
                for sample in samples
                if sample.name.startswith("security_gateway_inference_latency_seconds")
            ),
        ]
        return "\n".join(sections) + "\n"

    def _model_policy_samples(
        self, model_policies: dict[str, ModelPolicy]
    ) -> list[MetricSample]:
        return [
            MetricSample(
                "security_gateway_model_policy_info",
                {
                    "model_id": policy.model_id,
                    "sensitivity": policy.sensitivity,
                    "requires_reason": str(policy.requires_reason).lower(),
                },
                1,
            )
            for policy in sorted(model_policies.values(), key=lambda policy: policy.model_id)
        ]

    def _request_samples(self) -> list[MetricSample]:
        return [
            MetricSample(
                "security_gateway_requests_total",
                {"model_id": model_id, "outcome": outcome},
                count,
            )
            for (model_id, outcome), count in sorted(self._requests.items())
        ]

    def _denial_samples(self) -> list[MetricSample]:
        return [
            MetricSample(
                "security_gateway_denials_total",
                {"model_id": model_id, "reason": reason},
                count,
            )
            for (model_id, reason), count in sorted(self._denials.items())
        ]

    def _latency_samples(self) -> list[MetricSample]:
        samples: list[MetricSample] = []
        for model_id in sorted(self._latency_count):
            for bucket in self.latency_buckets:
                samples.append(
                    MetricSample(
                        "security_gateway_inference_latency_seconds_bucket",
                        {"model_id": model_id, "le": format_bucket(bucket)},
                        self._latency_buckets[(model_id, bucket)],
                    )
                )
            samples.append(
                MetricSample(
                    "security_gateway_inference_latency_seconds_bucket",
                    {"model_id": model_id, "le": "+Inf"},
                    self._latency_count[model_id],
                )
            )
            samples.append(
                MetricSample(
                    "security_gateway_inference_latency_seconds_count",
                    {"model_id": model_id},
                    self._latency_count[model_id],
                )
            )
            samples.append(
                MetricSample(
                    "security_gateway_inference_latency_seconds_sum",
                    {"model_id": model_id},
                    self._latency_sum[model_id],
                )
            )
        return samples


def format_samples(samples: Iterable[MetricSample]) -> list[str]:
    return [format_sample(sample) for sample in samples]


def format_sample(sample: MetricSample) -> str:
    labels = ",".join(
        f'{key}="{escape_label_value(value)}"' for key, value in sorted(sample.labels.items())
    )
    label_block = f"{{{labels}}}" if labels else ""
    return f"{sample.name}{label_block} {format_number(sample.value)}"


def escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def format_bucket(value: float) -> str:
    return f"{value:g}"


def format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")
