from __future__ import annotations

import argparse
import html
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


COLORS = (
    "#2563eb",
    "#dc2626",
    "#059669",
    "#d97706",
    "#7c3aed",
    "#0891b2",
    "#4b5563",
    "#db2777",
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def percentile(values: Iterable[float], percent: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def distribution(values: Iterable[float | int | None]) -> dict[str, Any]:
    numbers = [float(value) for value in values if isinstance(value, int | float)]
    if not numbers:
        return {
            "count": 0,
            "mean": None,
            "stddev": None,
            "min": None,
            "max": None,
            "p50": None,
            "p95": None,
            "p99": None,
        }
    return {
        "count": len(numbers),
        "mean": round(statistics.fmean(numbers), 6),
        "stddev": round(statistics.pstdev(numbers), 6),
        "min": round(min(numbers), 6),
        "max": round(max(numbers), 6),
        "p50": rounded(percentile(numbers, 50)),
        "p95": rounded(percentile(numbers, 95)),
        "p99": rounded(percentile(numbers, 99)),
    }


def analyze_run(run_dir: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    if config is None:
        config = json.loads(
            (run_dir / "config.resolved.json").read_text(encoding="utf-8")
        )
    samples = load_jsonl(run_dir / "raw_samples.jsonl")
    conditions = load_jsonl(run_dir / "conditions.jsonl")
    resources = load_jsonl(run_dir / "resource_samples.jsonl")
    if not samples or not conditions:
        raise ValueError("run directory has no completed benchmark samples")

    condition_by_id = {row["condition_id"]: row for row in conditions}
    groups = summarize_groups(samples, conditions, resources, condition_by_id)
    comparisons = compare_groups(samples, groups, config)
    headline = build_headline(comparisons, config)
    summary = {
        "schema_version": 1,
        "source": "vllm bench serve --save-detailed",
        "groups": groups,
        "comparisons": comparisons,
        "headline": headline,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_plots(run_dir, groups, comparisons)
    environment = json.loads((run_dir / "environment.json").read_text(encoding="utf-8"))
    (run_dir / "report.md").write_text(
        render_report(summary, environment, config),
        encoding="utf-8",
    )
    return summary


def summarize_groups(
    samples: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    condition_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    sample_groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    condition_groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(
        list
    )
    resource_groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(
        list
    )
    for sample in samples:
        sample_groups[group_key(sample)].append(sample)
    for condition in conditions:
        if condition.get("status") == "completed":
            condition_groups[group_key(condition)].append(condition)
    for resource in resources:
        condition = condition_by_id.get(resource.get("condition_id"))
        if condition is not None:
            resource_groups[group_key(condition)].append(resource)

    groups = []
    for key in sorted(sample_groups, key=lambda item: (item[1], item[2], item[0])):
        path, workload, concurrency = key
        rows = sample_groups[key]
        successful = [row for row in rows if row.get("success")]
        condition_rows = condition_groups.get(key, [])
        duration = sum(
            float(row.get("duration_seconds") or 0) for row in condition_rows
        )
        completed = sum(
            int(row.get("completed_requests") or 0) for row in condition_rows
        )
        failed = sum(int(row.get("failed_requests") or 0) for row in condition_rows)
        output_tokens = sum(
            int(row.get("total_output_tokens") or 0) for row in condition_rows
        )
        total_tokens = sum(
            int(row.get("total_output_tokens") or 0)
            + int(row.get("total_input_tokens") or 0)
            for row in condition_rows
        )
        run_p50s = []
        run_p95s = []
        run_throughputs = []
        by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in successful:
            by_condition[row["condition_id"]].append(row)
        for condition_id, condition_samples in by_condition.items():
            latencies = [row["total_latency_ms"] for row in condition_samples]
            run_p50s.append(percentile(latencies, 50))
            run_p95s.append(percentile(latencies, 95))
            condition = condition_by_id.get(condition_id, {})
            if condition.get("request_throughput") is not None:
                run_throughputs.append(float(condition["request_throughput"]))
        limiter = combine_limiter_latency(condition_rows)
        groups.append(
            {
                "path": path,
                "workload": workload,
                "concurrency": concurrency,
                "repetitions": len(condition_rows),
                "requests": len(rows),
                "completed_requests": completed,
                "failed_requests": failed,
                "error_rate": round(failed / max(completed + failed, 1), 8),
                "request_throughput": round(completed / duration, 6)
                if duration
                else None,
                "output_token_throughput": (
                    round(output_tokens / duration, 6) if duration else None
                ),
                "total_token_throughput": (
                    round(total_tokens / duration, 6) if duration else None
                ),
                "latency_ms": distribution(
                    row.get("total_latency_ms") for row in successful
                ),
                "ttft_ms": distribution(row.get("ttft_ms") for row in successful),
                "tpot_ms": distribution(row.get("tpot_ms") for row in successful),
                "itl_ms": distribution(
                    value
                    for row in successful
                    for value in row.get("inter_token_latency_ms", [])
                ),
                "prompt_tokens": distribution(
                    row.get("prompt_tokens") for row in successful
                ),
                "completion_tokens": distribution(
                    row.get("completion_tokens") for row in successful
                ),
                "run_variation": {
                    "p50_latency_stddev_ms": population_stddev(run_p50s),
                    "p95_latency_stddev_ms": population_stddev(run_p95s),
                    "request_throughput_stddev": population_stddev(run_throughputs),
                },
                "resources": summarize_resource_group(resource_groups.get(key, [])),
                "gateway_limiter_latency": limiter,
            }
        )
    return groups


def compare_groups(
    samples: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    indexed = {
        (group["workload"], group["concurrency"], group["path"]): group
        for group in groups
    }
    sample_index: dict[tuple[str, int, int, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    for sample in samples:
        sample_index[
            (
                sample["workload"],
                sample["concurrency"],
                sample["repetition"],
                sample["path"],
            )
        ].append(sample)

    comparisons = []
    for workload in [item["name"] for item in config["workloads"]]:
        for concurrency in config["concurrency"]:
            direct = indexed.get((workload, int(concurrency), "direct"))
            gateway = indexed.get((workload, int(concurrency), "gateway"))
            if direct is None or gateway is None:
                continue
            equivalent, equivalence_notes = workload_equivalence(
                sample_index,
                workload,
                int(concurrency),
                int(config["repetitions"]),
            )
            comparisons.append(
                {
                    "workload": workload,
                    "concurrency": int(concurrency),
                    "workload_equivalent": equivalent,
                    "equivalence_notes": equivalence_notes,
                    "direct": direct,
                    "gateway": gateway,
                    "gateway_overhead_ms": {
                        percentile_name: difference(
                            gateway["latency_ms"][percentile_name],
                            direct["latency_ms"][percentile_name],
                        )
                        for percentile_name in ("p50", "p95", "p99")
                    },
                    "ttft_overhead_ms": {
                        percentile_name: difference(
                            gateway["ttft_ms"][percentile_name],
                            direct["ttft_ms"][percentile_name],
                        )
                        for percentile_name in ("p50", "p95", "p99")
                    },
                    "throughput_difference_percent": percent_change(
                        gateway["request_throughput"],
                        direct["request_throughput"],
                    ),
                    "throughput_retained_percent": ratio_percent(
                        gateway["request_throughput"],
                        direct["request_throughput"],
                    ),
                    "output_token_throughput_difference_percent": percent_change(
                        gateway["output_token_throughput"],
                        direct["output_token_throughput"],
                    ),
                }
            )
    return comparisons


def workload_equivalence(
    sample_index: dict[tuple[str, int, int, str], list[dict[str, Any]]],
    workload: str,
    concurrency: int,
    repetitions: int,
) -> tuple[bool, list[str]]:
    notes = []
    equivalent = True
    for repetition in range(repetitions):
        direct = sample_index.get((workload, concurrency, repetition, "direct"), [])
        gateway = sample_index.get((workload, concurrency, repetition, "gateway"), [])
        direct_shape = sorted(
            (row["request_index"], row["prompt_tokens"], row["requested_output_tokens"])
            for row in direct
        )
        gateway_shape = sorted(
            (row["request_index"], row["prompt_tokens"], row["requested_output_tokens"])
            for row in gateway
        )
        if direct_shape != gateway_shape:
            equivalent = False
            notes.append(f"repetition {repetition} token-shape mismatch")
    if equivalent:
        notes.append(
            "paired seeds, request counts, prompt-token counts, and output limits match"
        )
    return equivalent, notes


def build_headline(
    comparisons: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    target = config.get("headline", {})
    match = next(
        (
            comparison
            for comparison in comparisons
            if comparison["workload"] == target.get("workload")
            and comparison["concurrency"] == int(target.get("concurrency", -1))
        ),
        None,
    )
    if match is None:
        return {"status": "unavailable", "reason": "predeclared condition was not run"}
    gates = {
        "workload_equivalent": match["workload_equivalent"],
        "at_least_two_repetitions": min(
            match["direct"]["repetitions"], match["gateway"]["repetitions"]
        )
        >= 2,
        "at_least_30_successes_per_path": min(
            match["direct"]["completed_requests"],
            match["gateway"]["completed_requests"],
        )
        >= 30,
        "zero_errors": (
            match["direct"]["failed_requests"] == 0
            and match["gateway"]["failed_requests"] == 0
        ),
    }
    if not all(gates.values()):
        return {
            "status": "held",
            "reason": "predeclared defensibility gates did not all pass",
            "gates": gates,
        }
    overhead = match["gateway_overhead_ms"]["p50"]
    retained = match["throughput_retained_percent"]
    return {
        "status": "supported",
        "gates": gates,
        "workload": match["workload"],
        "concurrency": match["concurrency"],
        "p50_overhead_ms": overhead,
        "throughput_retained_percent": retained,
        "text": (
            f"At {match['concurrency']} concurrent {match['workload']} requests, "
            f"the full gateway added {format_number(overhead)} ms p50 end-to-end "
            f"latency and retained {format_number(retained)}% of direct-vLLM "
            "request throughput."
        ),
    }


def render_report(
    summary: dict[str, Any],
    environment: dict[str, Any],
    config: dict[str, Any],
) -> str:
    lines = [
        "# vLLM gateway overhead benchmark",
        "",
        "This report contains measured results from the official `vllm bench serve` "
        "client. Direct and gateway paths used paired seeds, token lengths, request "
        "counts, concurrency, warmups, and model configuration.",
        "",
        "## Predeclared headline condition",
        "",
    ]
    headline = summary["headline"]
    if headline["status"] == "supported":
        lines.append(headline["text"])
    else:
        lines.append(f"No portfolio headline is supported: {headline['reason']}.")
    lines.extend(
        [
            "",
            "## Direct versus full gateway",
            "",
            "| Workload | C | Direct req/s | Gateway req/s | Req/s change | "
            "Direct output tok/s | Gateway output tok/s | p50 overhead | p95 overhead | "
            "p99 overhead | TTFT p50 overhead | Errors direct/gateway | Fair |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
        ]
    )
    for comparison in summary["comparisons"]:
        direct = comparison["direct"]
        gateway = comparison["gateway"]
        overhead = comparison["gateway_overhead_ms"]
        lines.append(
            "| {workload} | {concurrency} | {direct_rps} | {gateway_rps} | "
            "{change}% | {direct_tps} | {gateway_tps} | {p50} ms | {p95} ms | "
            "{p99} ms | {ttft} ms | {direct_errors}/{gateway_errors} | {fair} |".format(
                workload=comparison["workload"],
                concurrency=comparison["concurrency"],
                direct_rps=format_number(direct["request_throughput"]),
                gateway_rps=format_number(gateway["request_throughput"]),
                change=format_signed(comparison["throughput_difference_percent"]),
                direct_tps=format_number(direct["output_token_throughput"]),
                gateway_tps=format_number(gateway["output_token_throughput"]),
                p50=format_signed(overhead["p50"]),
                p95=format_signed(overhead["p95"]),
                p99=format_signed(overhead["p99"]),
                ttft=format_signed(comparison["ttft_overhead_ms"]["p50"]),
                direct_errors=direct["failed_requests"],
                gateway_errors=gateway["failed_requests"],
                fair="yes" if comparison["workload_equivalent"] else "no",
            )
        )

    gpu = environment.get("gpu", {})
    devices = gpu.get("devices", [])
    gpu_text = (
        ", ".join(
            f"{device.get('name')} ({device.get('memory_total_mib')} MiB)"
            for device in devices
        )
        or "unavailable"
    )
    software = environment.get("software", {})
    torch_runtime = environment.get("torch_runtime", {})
    gateway_runtime = environment.get("python_environments", {}).get("gateway", {})
    gateway_packages = gateway_runtime.get("packages", {})
    redis_environment = environment.get("redis", {})
    gateway_configuration = environment.get("gateway", {})
    model = environment.get("model", {})
    lines.extend(
        [
            "",
            "## Environment",
            "",
            f"- Git commit: `{environment.get('git', {}).get('commit')}` "
            f"(dirty: `{environment.get('git', {}).get('dirty')}`)",
            f"- OS: `{environment.get('operating_system', {}).get('platform')}`",
            f"- CPU: `{environment.get('cpu', {}).get('model')}`",
            f"- RAM: `{environment.get('system_ram_bytes')}` bytes",
            f"- GPU: `{gpu_text}`",
            f"- NVIDIA driver / CUDA: `{driver_versions(devices)}` / "
            f"`{gpu.get('cuda_version')}`",
            f"- PyTorch / CUDA build: `{torch_runtime.get('version')}` / "
            f"`{torch_runtime.get('cuda_build')}`",
            f"- Python / vLLM: `{environment.get('python', {}).get('version')}` / "
            f"`{software.get('vllm')}`",
            f"- Gateway Python / FastAPI / httpx: `{gateway_runtime.get('python')}` / "
            f"`{gateway_packages.get('fastapi')}` / `{gateway_packages.get('httpx')}`",
            f"- Model / revision: `{model.get('id')}` / `{model.get('revision')}`",
            f"- dtype / quantization: `{model.get('dtype')}` / `{model.get('quantization')}`",
            f"- Redis: `{redis_environment.get('version')}`; configuration: "
            f"`{json.dumps(redis_environment.get('configuration'), sort_keys=True)}`",
            f"- Gateway controls: "
            f"`{json.dumps(gateway_configuration, sort_keys=True)}`",
            "",
            "## Plots",
            "",
            "- [Tail latency versus concurrency](plots/latency-vs-concurrency.svg)",
            "- [Request throughput versus concurrency](plots/throughput-vs-concurrency.svg)",
            "- [TTFT versus concurrency](plots/ttft-vs-concurrency.svg)",
            "- [Output-token throughput versus concurrency](plots/token-throughput-vs-concurrency.svg)",
            "- [Gateway overhead versus concurrency](plots/gateway-overhead-vs-concurrency.svg)",
            "",
            "## Interpretation constraints",
            "",
            "- Absolute overhead is the gateway-path percentile minus the matching "
            "direct-path percentile; requests are distribution-paired by condition, "
            "not one-to-one latency-subtracted while sharing a live GPU scheduler.",
            "- Throughput aggregates completed requests or output tokens over the sum "
            "of official client-measured condition durations.",
            "- TTFT, TPOT, and ITL use vLLM's streaming client definitions. ITL is "
            "measured between non-empty streamed choice events.",
            "- Redis limiter percentile values, when present, are conservative "
            "Prometheus histogram upper bounds; the mean comes from counter deltas.",
            "- Authentication and transport costs are exactly those in the recorded "
            "gateway configuration; local HS256 and loopback HTTP do not measure TLS, "
            "JWKS retrieval, a load balancer, or cross-host network latency.",
            "- These results characterize only the recorded host, model, server flags, "
            "and gateway configuration. They are not a production fleet claim.",
            "",
        ]
    )
    return "\n".join(lines)


def write_plots(
    run_dir: Path,
    groups: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
) -> None:
    plots_dir = run_dir / "plots"
    plots_dir.mkdir(exist_ok=True)
    metrics = [
        (
            "latency-vs-concurrency.svg",
            "p95 end-to-end latency versus concurrency",
            "Latency (ms)",
            lambda group: group["latency_ms"]["p95"],
        ),
        (
            "throughput-vs-concurrency.svg",
            "Completed request throughput versus concurrency",
            "Requests / second",
            lambda group: group["request_throughput"],
        ),
        (
            "ttft-vs-concurrency.svg",
            "p50 time to first token versus concurrency",
            "TTFT (ms)",
            lambda group: group["ttft_ms"]["p50"],
        ),
        (
            "token-throughput-vs-concurrency.svg",
            "Output-token throughput versus concurrency",
            "Output tokens / second",
            lambda group: group["output_token_throughput"],
        ),
    ]
    for filename, title, y_label, getter in metrics:
        series = group_series(groups, getter)
        write_svg_chart(plots_dir / filename, title, y_label, series)

    overhead_series = []
    by_workload: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for comparison in comparisons:
        by_workload[comparison["workload"]].append(comparison)
    for workload, rows in sorted(by_workload.items()):
        ordered = sorted(rows, key=lambda row: row["concurrency"])
        for metric in ("p50", "p95"):
            overhead_series.append(
                {
                    "label": f"{workload} {metric}",
                    "points": [
                        (row["concurrency"], row["gateway_overhead_ms"][metric])
                        for row in ordered
                    ],
                }
            )
    write_svg_chart(
        plots_dir / "gateway-overhead-vs-concurrency.svg",
        "Gateway-added end-to-end latency versus concurrency",
        "Gateway overhead (ms)",
        overhead_series,
        include_zero=True,
    )


def group_series(groups: list[dict[str, Any]], getter: Any) -> list[dict[str, Any]]:
    indexed: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for group in groups:
        indexed[(group["workload"], group["path"])].append(group)
    return [
        {
            "label": f"{workload} {path}",
            "points": [
                (row["concurrency"], getter(row))
                for row in sorted(rows, key=lambda item: item["concurrency"])
            ],
        }
        for (workload, path), rows in sorted(indexed.items())
    ]


def write_svg_chart(
    path: Path,
    title: str,
    y_label: str,
    series: list[dict[str, Any]],
    *,
    include_zero: bool = False,
) -> None:
    width, height = 1000, 620
    left, right, top, bottom = 90, 240, 70, 75
    plot_width = width - left - right
    plot_height = height - top - bottom
    x_values = sorted(
        {int(x) for item in series for x, y in item["points"] if y is not None}
    )
    y_values = [float(y) for item in series for _, y in item["points"] if y is not None]
    if not x_values or not y_values:
        path.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
            f'<text x="40" y="60">{html.escape(title)}: no data</text></svg>\n',
            encoding="utf-8",
        )
        return
    y_min = min(y_values)
    y_max = max(y_values)
    if include_zero:
        y_min = min(0.0, y_min)
        y_max = max(0.0, y_max)
    padding = max((y_max - y_min) * 0.1, abs(y_max) * 0.02, 1e-9)
    y_min -= padding
    y_max += padding

    def x_position(value: int) -> float:
        if len(x_values) == 1:
            return left + plot_width / 2
        return left + x_values.index(value) * plot_width / (len(x_values) - 1)

    def y_position(value: float) -> float:
        return top + (y_max - value) * plot_height / (y_max - y_min)

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="35" font-family="sans-serif" font-size="22" font-weight="600">{html.escape(title)}</text>',
    ]
    for tick in range(6):
        value = y_min + (y_max - y_min) * tick / 5
        y = y_position(value)
        svg.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_width}" y2="{y:.2f}" stroke="#e5e7eb"/>'
        )
        svg.append(
            f'<text x="{left - 10}" y="{y + 4:.2f}" text-anchor="end" font-family="sans-serif" font-size="12">{value:.2f}</text>'
        )
    for value in x_values:
        x = x_position(value)
        svg.append(
            f'<text x="{x:.2f}" y="{top + plot_height + 25}" text-anchor="middle" font-family="sans-serif" font-size="12">{value}</text>'
        )
    svg.extend(
        [
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#111827"/>',
            f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#111827"/>',
            f'<text x="{left + plot_width / 2}" y="{height - 20}" text-anchor="middle" font-family="sans-serif" font-size="14">Concurrency</text>',
            f'<text transform="translate(22 {top + plot_height / 2}) rotate(-90)" text-anchor="middle" font-family="sans-serif" font-size="14">{html.escape(y_label)}</text>',
        ]
    )
    for index, item in enumerate(series):
        color = COLORS[index % len(COLORS)]
        points = [
            (x_position(int(x)), y_position(float(y)))
            for x, y in item["points"]
            if y is not None
        ]
        if points:
            svg.append(
                f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="'
                + " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
                + '"/>'
            )
            for x, y in points:
                svg.append(
                    f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.5" fill="{color}"/>'
                )
        legend_y = top + index * 24
        svg.append(
            f'<line x1="{left + plot_width + 25}" y1="{legend_y}" x2="{left + plot_width + 50}" y2="{legend_y}" stroke="{color}" stroke-width="3"/>'
        )
        svg.append(
            f'<text x="{left + plot_width + 60}" y="{legend_y + 4}" font-family="sans-serif" font-size="12">{html.escape(item["label"])}</text>'
        )
    svg.append("</svg>")
    path.write_text("\n".join(svg) + "\n", encoding="utf-8")


def summarize_resource_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    gpu_rows = [gpu for row in rows for gpu in row.get("gpus", [])]
    return {
        "sample_count": len(rows),
        "system_cpu_percent": distribution(
            row.get("system_cpu_percent") for row in rows
        ),
        "system_memory_used_bytes": distribution(
            row.get("system_memory_used_bytes") for row in rows
        ),
        "gpu_utilization_percent": distribution(
            row.get("utilization_percent") for row in gpu_rows
        ),
        "gpu_memory_used_mib": distribution(
            row.get("memory_used_mib") for row in gpu_rows
        ),
        "processes": summarize_processes(rows),
    }


def summarize_processes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    names = sorted({name for row in rows for name in row.get("processes", {})})
    result = {}
    for name in names:
        samples = [row.get("processes", {}).get(name, {}) for row in rows]
        result[name] = {
            "cpu_percent": distribution(
                sample.get("cpu_percent") for sample in samples
            ),
            "rss_bytes": distribution(sample.get("rss_bytes") for sample in samples),
        }
    return result


def combine_limiter_latency(conditions: list[dict[str, Any]]) -> dict[str, Any] | None:
    entries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for condition in conditions:
        for name, value in (condition.get("gateway_limiter_latency") or {}).items():
            entries[name].append(value)
    if not entries:
        return None
    result = {}
    for name, rows in entries.items():
        count = sum(int(row.get("count") or 0) for row in rows)
        weighted_sum = sum(
            float(row.get("mean_ms") or 0) * int(row.get("count") or 0) for row in rows
        )
        result[name] = {
            "backend": rows[0].get("backend"),
            "count": count,
            "mean_ms": round(weighted_sum / count, 6) if count else None,
            "p50_upper_bound_ms": max_present(
                row.get("p50_upper_bound_ms") for row in rows
            ),
            "p95_upper_bound_ms": max_present(
                row.get("p95_upper_bound_ms") for row in rows
            ),
            "p99_upper_bound_ms": max_present(
                row.get("p99_upper_bound_ms") for row in rows
            ),
        }
    return result


def group_key(row: dict[str, Any]) -> tuple[str, str, int]:
    return row["path"], row["workload"], int(row["concurrency"])


def difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(float(left) - float(right), 6)


def percent_change(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline in (None, 0):
        return None
    return round((float(value) / float(baseline) - 1) * 100, 6)


def ratio_percent(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline in (None, 0):
        return None
    return round(float(value) / float(baseline) * 100, 6)


def rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def population_stddev(values: Iterable[float | None]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return None
    return round(statistics.pstdev(numbers), 6)


def max_present(values: Iterable[float | None]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    return max(numbers) if numbers else None


def format_number(value: float | int | None) -> str:
    return "n/a" if value is None else f"{float(value):.2f}"


def format_signed(value: float | int | None) -> str:
    return "n/a" if value is None else f"{float(value):+.2f}"


def driver_versions(devices: list[dict[str, Any]]) -> str:
    versions = sorted({str(device.get("driver_version")) for device in devices})
    return ",".join(versions) if versions else "unavailable"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze a completed vLLM gateway run."
    )
    parser.add_argument("run_dir", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    analyze_run(args.run_dir.resolve())
    print(args.run_dir.resolve() / "report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
