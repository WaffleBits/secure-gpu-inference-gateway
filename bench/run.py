from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench.environment import (
    ResourceSampler,
    capture_environment,
    summarize_resources,
    utc_now,
    write_jsonl,
)


ROOT = Path(__file__).resolve().parents[1]
PROMETHEUS_SAMPLE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)$"
)
LABEL = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>(?:\\.|[^"])*)"')


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    required = (
        "schema_version",
        "vllm_command",
        "paths",
        "model",
        "workloads",
        "concurrency",
        "repetitions",
        "seed",
    )
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"benchmark config is missing: {', '.join(missing)}")
    if config["schema_version"] != 1:
        raise ValueError("unsupported benchmark config schema")
    if not isinstance(config["vllm_command"], list) or not config["vllm_command"]:
        raise ValueError("vllm_command must be a non-empty argument list")
    if set(config["paths"]) != {"direct", "gateway"}:
        raise ValueError("paths must define exactly direct and gateway")
    if not isinstance(config.get("gateway_configuration", {}), dict):
        raise ValueError("gateway_configuration must be an object")
    for path_name, path_config in config["paths"].items():
        if not str(path_config.get("base_url", "")).startswith(("http://", "https://")):
            raise ValueError(f"{path_name} base_url must be HTTP(S)")
        if not isinstance(path_config.get("headers", {}), dict):
            raise ValueError(f"{path_name} headers must be an object")
    model = config["model"]
    if not model.get("id") or not model.get("served_name"):
        raise ValueError("model id and served_name are required")
    vllm_environment = model.get("vllm_environment", {})
    if not isinstance(vllm_environment, dict):
        raise ValueError("model.vllm_environment must be an object")
    for name, value in vllm_environment.items():
        if not isinstance(name, str) or not name.startswith("VLLM_"):
            raise ValueError("model.vllm_environment keys must start with VLLM_")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError("model.vllm_environment values must be scalar")
    workload_names = set()
    for workload in config["workloads"]:
        name = workload.get("name")
        if not name or name in workload_names:
            raise ValueError("workload names must be non-empty and unique")
        workload_names.add(name)
        if int(workload.get("input_tokens", 0)) < 1:
            raise ValueError(f"{name} input_tokens must be positive")
        if int(workload.get("output_tokens", 0)) < 1:
            raise ValueError(f"{name} output_tokens must be positive")
        if "min_requests" in workload and int(workload["min_requests"]) < 1:
            raise ValueError(f"{name} min_requests must be positive")
    concurrency = [int(value) for value in config["concurrency"]]
    if not concurrency or any(value < 1 for value in concurrency):
        raise ValueError("concurrency values must be positive")
    if len(concurrency) != len(set(concurrency)):
        raise ValueError("concurrency values must be unique")
    if int(config["repetitions"]) < 1:
        raise ValueError("repetitions must be positive")
    if float(config.get("sample_interval_seconds", 0.5)) <= 0:
        raise ValueError("sample_interval_seconds must be positive")
    for command_name in ("git_command", "nvidia_smi_command"):
        command = config.get(command_name)
        if command is not None and (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ValueError(f"{command_name} must be a non-empty argument list")
    python_environments = config.get("python_environments", {})
    if not isinstance(python_environments, dict):
        raise ValueError("python_environments must be an object")
    for name, command in python_environments.items():
        if not isinstance(name, str) or not name:
            raise ValueError("python_environments keys must be non-empty strings")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(part, str) and part for part in command)
        ):
            raise ValueError(
                "python_environments values must be non-empty argument lists"
            )


def smoke_config(config: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(config)
    result["workloads"] = [result["workloads"][0]]
    result["workloads"][0]["min_requests"] = 2
    result["concurrency"] = [result["concurrency"][0]]
    result["repetitions"] = 1
    result["min_requests_per_condition"] = 2
    result["requests_per_concurrency"] = 1
    result["warmup_requests"] = 1
    result["headline"] = {
        "workload": result["workloads"][0]["name"],
        "concurrency": result["concurrency"][0],
    }
    result["run_name"] = f"{result.get('run_name', 'benchmark')}-smoke"
    return result


def build_schedule(config: dict[str, Any]) -> list[dict[str, Any]]:
    schedule: list[dict[str, Any]] = []
    for repetition in range(int(config["repetitions"])):
        for workload_index, workload in enumerate(config["workloads"]):
            for concurrency_index, concurrency in enumerate(config["concurrency"]):
                path_order = ["direct", "gateway"]
                if (repetition + workload_index + concurrency_index) % 2:
                    path_order.reverse()
                seed = int(config["seed"]) + repetition * 1000 + workload_index
                minimum_requests = int(
                    workload.get(
                        "min_requests",
                        config.get("min_requests_per_condition", 32),
                    )
                )
                request_count = max(
                    minimum_requests,
                    int(concurrency) * int(config.get("requests_per_concurrency", 2)),
                )
                for path_name in path_order:
                    schedule.append(
                        {
                            "path": path_name,
                            "repetition": repetition,
                            "workload": workload,
                            "concurrency": int(concurrency),
                            "seed": seed,
                            "request_count": request_count,
                        }
                    )
    return schedule


def condition_id(condition: dict[str, Any]) -> str:
    workload = safe_name(condition["workload"]["name"])
    return (
        f"r{condition['repetition']:02d}-{workload}-"
        f"c{condition['concurrency']:03d}-{condition['path']}"
    )


def build_vllm_command(
    config: dict[str, Any],
    condition: dict[str, Any],
    result_dir: Path,
    result_filename: str,
) -> list[str]:
    workload = condition["workload"]
    path_config = config["paths"][condition["path"]]
    command = [
        *[str(part) for part in config["vllm_command"]],
        "--backend",
        "openai",
        "--base-url",
        str(path_config["base_url"]).rstrip("/"),
        "--endpoint",
        "/v1/completions",
        "--model",
        str(config["model"]["id"]),
        "--served-model-name",
        str(config["model"]["served_name"]),
        "--dataset-name",
        "random",
        "--random-input-len",
        str(workload["input_tokens"]),
        "--random-output-len",
        str(workload["output_tokens"]),
        "--random-range-ratio",
        "0",
        "--num-prompts",
        str(condition["request_count"]),
        "--max-concurrency",
        str(condition["concurrency"]),
        "--request-rate",
        "inf",
        "--seed",
        str(condition["seed"]),
        "--temperature",
        "0",
        "--ignore-eos",
        "--num-warmups",
        str(config.get("warmup_requests", 4)),
        "--disable-tqdm",
        "--save-result",
        "--save-detailed",
        "--result-dir",
        str(result_dir),
        "--result-filename",
        result_filename,
        "--percentile-metrics",
        "ttft,tpot,itl,e2el",
        "--metric-percentiles",
        "50,95,99",
        "--request-id-prefix",
        f"sgig-{condition_id(condition)}-",
    ]
    for name, value in sorted(path_config.get("headers", {}).items()):
        command.extend(["--header", f"{name}={value}"])
    return command


def child_environment(config: dict[str, Any], path_name: str) -> dict[str, str]:
    child = os.environ.copy()
    child.pop("OPENAI_API_KEY", None)
    env_name = config["paths"][path_name].get("api_key_env")
    if env_name and os.getenv(env_name):
        child["OPENAI_API_KEY"] = os.environ[env_name]
    return child


def run_benchmark(config: dict[str, Any], run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=False)
    upstream_dir = run_dir / "upstream"
    logs_dir = run_dir / "logs"
    upstream_dir.mkdir()
    logs_dir.mkdir()

    resolved_config = sanitize_config(config)
    (run_dir / "config.resolved.json").write_text(
        json.dumps(resolved_config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    environment = capture_environment(config, ROOT)
    environment["endpoint_checks"] = probe_endpoints(config)
    (run_dir / "environment.json").write_text(
        json.dumps(environment, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    failed_checks = [
        name
        for name, check in environment["endpoint_checks"].items()
        if not check["reachable"]
    ]
    if failed_checks:
        raise RuntimeError(
            f"benchmark endpoints are unavailable: {', '.join(failed_checks)}"
        )

    for ordinal, condition in enumerate(build_schedule(config)):
        run_condition(config, condition, ordinal, run_dir, upstream_dir, logs_dir)

    from bench.analyze import analyze_run

    analyze_run(run_dir, resolved_config)


def run_condition(
    config: dict[str, Any],
    condition: dict[str, Any],
    ordinal: int,
    run_dir: Path,
    upstream_dir: Path,
    logs_dir: Path,
) -> None:
    identifier = condition_id(condition)
    result_filename = f"{identifier}.json"
    result_path = upstream_dir / result_filename
    command = build_vllm_command(
        config,
        condition,
        upstream_dir,
        result_filename,
    )
    before_metrics = (
        fetch_metrics(config.get("gateway_metrics_url"))
        if condition["path"] == "gateway"
        else None
    )
    started_at = utc_now()
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=child_environment(config, condition["path"]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    monitored_pids = {
        name: int(pid) for name, pid in config.get("monitored_pids", {}).items()
    }
    monitored_pids["benchmark_client"] = process.pid
    sampler = ResourceSampler(
        interval_seconds=float(config.get("sample_interval_seconds", 0.5)),
        nvidia_smi_command=[
            str(part) for part in config.get("nvidia_smi_command", ["nvidia-smi"])
        ],
        monitored_pids=monitored_pids,
    )
    sampler.start()
    timed_out = False
    try:
        stdout, stderr = process.communicate(
            timeout=float(config.get("condition_timeout_seconds", 7200))
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        stdout, stderr = process.communicate(timeout=30)
    resource_samples = sampler.stop()
    after_metrics = (
        fetch_metrics(config.get("gateway_metrics_url"))
        if condition["path"] == "gateway"
        else None
    )

    log_payload = {
        "condition_id": identifier,
        "command": redact_command(command),
        "return_code": process.returncode,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
    }
    (logs_dir / f"{identifier}.json").write_text(
        json.dumps(log_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for sample in resource_samples:
        sample.update({"condition_id": identifier, "path": condition["path"]})
    write_jsonl(run_dir / "resource_samples.jsonl", resource_samples)

    if process.returncode != 0 or timed_out or not result_path.exists():
        condition_row = base_condition_row(condition, identifier, ordinal, started_at)
        condition_row.update(
            {
                "status": "failed",
                "return_code": process.returncode,
                "timed_out": timed_out,
                "resource_summary": summarize_resources(resource_samples),
            }
        )
        write_jsonl(run_dir / "conditions.jsonl", [condition_row])
        if not config.get("continue_on_error", False):
            raise RuntimeError(f"benchmark condition failed: {identifier}")
        return

    result = json.loads(result_path.read_text(encoding="utf-8"))
    raw_samples = extract_raw_samples(result, condition, identifier)
    write_jsonl(run_dir / "raw_samples.jsonl", raw_samples)

    sanitized_upstream = sanitize_upstream_result(result)
    result_path.write_text(
        json.dumps(sanitized_upstream, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    condition_row = base_condition_row(condition, identifier, ordinal, started_at)
    condition_row.update(
        {
            "status": "completed",
            "duration_seconds": result.get("duration"),
            "completed_requests": result.get("completed"),
            "failed_requests": result.get("failed", 0),
            "request_throughput": result.get("request_throughput"),
            "output_token_throughput": result.get("output_throughput"),
            "total_token_throughput": result.get("total_token_throughput"),
            "total_input_tokens": result.get("total_input_tokens"),
            "total_output_tokens": result.get("total_output_tokens"),
            "resource_summary": summarize_resources(resource_samples),
            "gateway_limiter_latency": limiter_latency_delta(
                before_metrics,
                after_metrics,
            ),
        }
    )
    write_jsonl(run_dir / "conditions.jsonl", [condition_row])


def base_condition_row(
    condition: dict[str, Any],
    identifier: str,
    ordinal: int,
    started_at: str,
) -> dict[str, Any]:
    workload = condition["workload"]
    return {
        "schema_version": 1,
        "condition_id": identifier,
        "ordinal": ordinal,
        "started_at": started_at,
        "path": condition["path"],
        "repetition": condition["repetition"],
        "workload": workload["name"],
        "requested_input_tokens": workload["input_tokens"],
        "requested_output_tokens": workload["output_tokens"],
        "concurrency": condition["concurrency"],
        "seed": condition["seed"],
        "requested_requests": condition["request_count"],
    }


def extract_raw_samples(
    result: dict[str, Any],
    condition: dict[str, Any],
    identifier: str,
) -> list[dict[str, Any]]:
    input_lens = list(result.get("input_lens", []))
    output_lens = list(result.get("output_lens", []))
    ttfts = list(result.get("ttfts", []))
    itls = list(result.get("itls", []))
    start_times = list(result.get("start_times", []))
    errors = list(result.get("errors", []))
    lengths = {len(values) for values in (input_lens, output_lens, ttfts, itls, errors)}
    if len(lengths) != 1 or not lengths:
        raise ValueError(f"vLLM detailed arrays are misaligned for {identifier}")

    rows = []
    for index in range(len(input_lens)):
        error = str(errors[index] or "")
        inter_token_ms = [round(float(value) * 1000, 6) for value in itls[index]]
        ttft_ms = float(ttfts[index]) * 1000
        e2e_latency_ms = ttft_ms + sum(inter_token_ms)
        output_tokens = int(output_lens[index])
        tpot_ms = None
        if output_tokens > 1:
            tpot_ms = max(0.0, e2e_latency_ms - ttft_ms) / (output_tokens - 1)
        rows.append(
            {
                "schema_version": 1,
                "request_id": f"{identifier}-{index:06d}",
                "condition_id": identifier,
                "path": condition["path"],
                "repetition": condition["repetition"],
                "workload": condition["workload"]["name"],
                "concurrency": condition["concurrency"],
                "seed": condition["seed"],
                "request_index": index,
                "status": "failed" if error else "completed",
                "success": not error,
                "prompt_tokens": int(input_lens[index]),
                "requested_output_tokens": condition["workload"]["output_tokens"],
                "completion_tokens": output_tokens,
                "ttft_ms": round(ttft_ms, 6) if not error else None,
                "total_latency_ms": round(e2e_latency_ms, 6) if not error else None,
                "tpot_ms": round(tpot_ms, 6)
                if tpot_ms is not None and not error
                else None,
                "inter_token_latency_ms": inter_token_ms if not error else [],
                "client_start_time": (
                    start_times[index] if index < len(start_times) else None
                ),
                "error": sanitize_error(error),
            }
        )
    return rows


def sanitize_upstream_result(result: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(result)
    sanitized.pop("generated_texts", None)
    sanitized["errors"] = [
        sanitize_error(str(error or "")) for error in result.get("errors", [])
    ]
    return sanitized


def sanitize_error(error: str) -> str | None:
    if not error:
        return None
    compact = " ".join(error.split())
    compact = re.sub(r"https?://\S+", "<endpoint>", compact)
    return compact[:500]


def sanitize_config(config: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(config)
    for path_config in result.get("paths", {}).values():
        for name in list(path_config.get("headers", {})):
            if name.lower() in {"authorization", "x-api-key", "api-key"}:
                path_config["headers"][name] = "<redacted>"
    return result


def redact_command(command: list[str]) -> list[str]:
    redacted = list(command)
    for index, part in enumerate(redacted[:-1]):
        if part == "--header":
            name, separator, _ = redacted[index + 1].partition("=")
            if separator and name.lower() in {"authorization", "x-api-key", "api-key"}:
                redacted[index + 1] = f"{name}=<redacted>"
    return redacted


def probe_endpoints(config: dict[str, Any]) -> dict[str, Any]:
    checks = {}
    for path_name, suffix in (("direct", "/v1/models"), ("gateway", "/health")):
        path_config = config["paths"][path_name]
        url = str(path_config["base_url"]).rstrip("/") + suffix
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read(65536)
                payload = json.loads(body) if body else None
                checks[path_name] = {
                    "reachable": response.status == 200,
                    "status": response.status,
                    "model_ids": safe_model_ids(payload)
                    if path_name == "direct"
                    else [],
                }
        except (OSError, ValueError, urllib.error.URLError) as error:
            checks[path_name] = {
                "reachable": False,
                "status": None,
                "error_type": type(error).__name__,
                "model_ids": [],
            }
    return checks


def safe_model_ids(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    return [
        str(item["id"])
        for item in payload["data"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]


def fetch_metrics(
    url: str | None,
) -> dict[tuple[str, tuple[tuple[str, str], ...]], float] | None:
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            text = response.read().decode("utf-8")
    except (OSError, UnicodeDecodeError, urllib.error.URLError):
        return None
    return parse_prometheus(text)


def parse_prometheus(
    text: str,
) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    samples = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = PROMETHEUS_SAMPLE.match(line.strip())
        if match is None:
            continue
        labels = tuple(
            sorted(
                (label.group("key"), unescape_label(label.group("value")))
                for label in LABEL.finditer(match.group("labels") or "")
            )
        )
        samples[(match.group("name"), labels)] = float(match.group("value"))
    return samples


def unescape_label(value: str) -> str:
    return value.replace(r"\n", "\n").replace(r"\"", '"').replace(r"\\", "\\")


def limiter_latency_delta(
    before: dict[tuple[str, tuple[tuple[str, str], ...]], float] | None,
    after: dict[tuple[str, tuple[tuple[str, str], ...]], float] | None,
) -> dict[str, Any] | None:
    if before is None or after is None:
        return None
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for (name, labels_tuple), after_value in after.items():
        if not name.startswith("security_gateway_limiter_latency_seconds_"):
            continue
        labels = dict(labels_tuple)
        limiter = labels.get("limiter")
        backend = labels.get("backend")
        if not limiter or not backend:
            continue
        delta = after_value - before.get((name, labels_tuple), 0.0)
        group = groups.setdefault((limiter, backend), {"buckets": {}})
        if name.endswith("_count"):
            group["count"] = int(round(delta))
        elif name.endswith("_sum"):
            group["sum_seconds"] = delta
        elif name.endswith("_bucket"):
            group["buckets"][labels.get("le", "+Inf")] = delta

    result = {}
    for (limiter, backend), group in sorted(groups.items()):
        count = int(group.get("count", 0))
        sum_seconds = float(group.get("sum_seconds", 0.0))
        result[limiter] = {
            "backend": backend,
            "count": count,
            "mean_ms": round(sum_seconds * 1000 / count, 6) if count else None,
            "p50_upper_bound_ms": histogram_bound(group["buckets"], count, 50),
            "p95_upper_bound_ms": histogram_bound(group["buckets"], count, 95),
            "p99_upper_bound_ms": histogram_bound(group["buckets"], count, 99),
        }
    return result


def histogram_bound(
    buckets: dict[str, float], count: int, percent: float
) -> float | None:
    if count < 1:
        return None
    threshold = math.ceil(count * percent / 100)
    finite = sorted(
        (float(bound), value) for bound, value in buckets.items() if bound != "+Inf"
    )
    for bound, value in finite:
        if value >= threshold:
            return round(bound * 1000, 6)
    return None


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "run"


def default_run_dir(config: dict[str, Any]) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (
        ROOT
        / "bench"
        / "results"
        / f"{timestamp}-{safe_name(config.get('run_name', 'benchmark'))}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run paired direct-vLLM and full-gateway serving benchmarks."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "bench" / "config.example.json",
    )
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    if args.smoke:
        config = smoke_config(config)
    if args.dry_run:
        schedule = build_schedule(config)
        payload = [
            {
                **condition,
                "command": redact_command(
                    build_vllm_command(
                        config, condition, Path("<result-dir>"), "result.json"
                    )
                ),
            }
            for condition in schedule
        ]
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    run_dir = (args.run_dir or default_run_dir(config)).resolve()
    run_benchmark(config, run_dir)
    print(run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
