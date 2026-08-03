from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import statistics
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - exercised on minimal benchmark hosts
    psutil = None


PACKAGE_NAMES = ("vllm", "torch", "fastapi", "httpx", "redis", "psutil")
PYTHON_ENVIRONMENT_SCRIPT = """
import importlib.metadata
import json
import platform

names = ("vllm", "torch", "fastapi", "httpx", "redis", "psutil")
versions = {}
for name in names:
    try:
        versions[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        versions[name] = None
print(json.dumps({"python": platform.python_version(), "packages": versions}))
""".strip()
TORCH_RUNTIME_SCRIPT = """
import json
import torch

print(json.dumps({"version": torch.__version__, "cuda_build": torch.version.cuda}))
""".strip()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def capture_environment(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    model = config["model"]
    environment: dict[str, Any] = {
        "captured_at": utc_now(),
        "operating_system": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": os.path.basename(os.sys.executable),
        },
        "cpu": {
            "model": platform.processor() or None,
            "logical_cores": os.cpu_count(),
        },
        "system_ram_bytes": None,
        "gpu": query_nvidia(config.get("nvidia_smi_command", ["nvidia-smi"])),
        "software": {name: package_version(name) for name in PACKAGE_NAMES},
        "torch_runtime": capture_torch_runtime(),
        "python_environments": capture_python_environments(config),
        "git": capture_git(repo_root, config.get("git_command", ["git"])),
        "model": {
            "id": model["id"],
            "served_name": model["served_name"],
            "revision": model.get("revision"),
            "dtype": model.get("dtype"),
            "quantization": model.get("quantization"),
            "vllm_environment": model.get("vllm_environment", {}),
            "vllm_server_args": model.get("vllm_server_args", []),
        },
        "benchmark": {
            "vllm_command": config["vllm_command"],
            "seed": config["seed"],
            "repetitions": config["repetitions"],
            "concurrency": config["concurrency"],
            "workloads": config["workloads"],
        },
        "gateway": config.get("gateway_configuration", {}),
        "redis": capture_redis_version(config),
    }
    if psutil is not None:
        memory = psutil.virtual_memory()
        environment["system_ram_bytes"] = int(memory.total)
        environment["cpu"]["physical_cores"] = psutil.cpu_count(logical=False)
        environment["cpu"]["model"] = cpu_model() or environment["cpu"]["model"]
    return environment


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def capture_python_environments(config: dict[str, Any]) -> dict[str, Any]:
    captured = {}
    for name, command in sorted(config.get("python_environments", {}).items()):
        output = run_text([*command, "-c", PYTHON_ENVIRONMENT_SCRIPT], timeout=15)
        if output is None:
            captured[name] = {"available": False}
            continue
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            captured[name] = {"available": False}
            continue
        captured[name] = {"available": True, **payload}
    return captured


def capture_torch_runtime() -> dict[str, Any]:
    output = run_text([os.sys.executable, "-c", TORCH_RUNTIME_SCRIPT], timeout=15)
    if output is None:
        return {"available": False, "version": None, "cuda_build": None}
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return {"available": False, "version": None, "cuda_build": None}
    return {"available": True, **payload}


def capture_git(repo_root: Path, command: list[str]) -> dict[str, Any]:
    commit = run_text([*command, "rev-parse", "HEAD"], cwd=repo_root)
    branch = run_text([*command, "branch", "--show-current"], cwd=repo_root)
    status = run_text([*command, "status", "--porcelain"], cwd=repo_root)
    return {
        "commit": commit,
        "branch": branch,
        "dirty": bool(status),
    }


def cpu_model() -> str | None:
    if platform.system() == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("model name"):
                    return line.partition(":")[2].strip()
        except OSError:
            pass
    return platform.processor() or None


def query_nvidia(command: list[str]) -> dict[str, Any]:
    query = [
        *command,
        "--query-gpu=name,uuid,memory.total,driver_version,compute_cap",
        "--format=csv,noheader,nounits",
    ]
    output = run_text(query, timeout=10)
    if output is None:
        return {"available": False, "devices": [], "cuda_version": None}

    devices = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) >= 5:
            devices.append(
                {
                    "name": fields[0],
                    "uuid": fields[1],
                    "memory_total_mib": number(fields[2]),
                    "driver_version": fields[3],
                    "compute_capability": fields[4],
                }
            )
    version_output = run_text([*command], timeout=10) or ""
    cuda_version = None
    marker = "CUDA Version:"
    if marker in version_output:
        cuda_version = version_output.split(marker, 1)[1].split()[0]
    return {
        "available": bool(devices),
        "devices": devices,
        "cuda_version": cuda_version,
    }


def capture_redis_version(config: dict[str, Any]) -> dict[str, Any]:
    env_name = config.get("redis_url_env")
    redis_url = os.getenv(env_name, "") if env_name else ""
    if not redis_url:
        return {
            "configured": False,
            "version": None,
            "ping_latency_ms": None,
            "configuration": None,
        }
    try:
        import redis

        client = redis.Redis.from_url(redis_url, socket_timeout=2)
        started = time.perf_counter()
        client.ping()
        ping_latency_ms = (time.perf_counter() - started) * 1000
        info = client.info(section="server")
        configuration = {}
        for name in ("save", "appendonly", "appendfsync", "maxmemory-policy"):
            value = client.config_get(name).get(name)
            configuration[name] = value.decode() if isinstance(value, bytes) else value
        return {
            "configured": True,
            "version": info.get("redis_version"),
            "ping_latency_ms": round(ping_latency_ms, 3),
            "configuration": configuration,
        }
    except Exception as error:  # Redis is optional; record the bounded failure.
        return {
            "configured": True,
            "version": None,
            "ping_latency_ms": None,
            "configuration": None,
            "error_type": type(error).__name__,
        }


class ResourceSampler:
    def __init__(
        self,
        *,
        interval_seconds: float,
        nvidia_smi_command: list[str],
        monitored_pids: dict[str, int],
    ) -> None:
        self.interval_seconds = interval_seconds
        self.nvidia_smi_command = nvidia_smi_command
        self.monitored_pids = monitored_pids
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._processes: dict[str, Any] = {}

    def start(self) -> None:
        if psutil is not None:
            psutil.cpu_percent(interval=None)
            for name, pid in self.monitored_pids.items():
                try:
                    process = psutil.Process(pid)
                    process.cpu_percent(interval=None)
                    self._processes[name] = process
                except (psutil.Error, ValueError):
                    pass
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> list[dict[str, Any]]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.interval_seconds * 2))
        return self.samples

    def _run(self) -> None:
        while not self._stop.is_set():
            self.samples.append(self._sample())
            self._stop.wait(self.interval_seconds)

    def _sample(self) -> dict[str, Any]:
        sample: dict[str, Any] = {
            "timestamp": utc_now(),
            "system_cpu_percent": None,
            "system_memory_used_bytes": None,
            "system_memory_percent": None,
            "processes": {},
            "gpus": query_gpu_utilization(self.nvidia_smi_command),
        }
        if psutil is None:
            return sample

        memory = psutil.virtual_memory()
        sample["system_cpu_percent"] = psutil.cpu_percent(interval=None)
        sample["system_memory_used_bytes"] = int(memory.used)
        sample["system_memory_percent"] = float(memory.percent)
        for name, pid in self.monitored_pids.items():
            try:
                process = self._processes.get(name)
                if process is None:
                    process = psutil.Process(int(pid))
                    process.cpu_percent(interval=None)
                    self._processes[name] = process
                with process.oneshot():
                    sample["processes"][name] = {
                        "pid": int(pid),
                        "cpu_percent": process.cpu_percent(interval=None),
                        "rss_bytes": int(process.memory_info().rss),
                    }
            except (psutil.Error, ValueError):
                sample["processes"][name] = {
                    "pid": int(pid),
                    "unavailable": True,
                }
        return sample


def query_gpu_utilization(command: list[str]) -> list[dict[str, Any]]:
    output = run_text(
        [
            *command,
            "--query-gpu=index,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu",
            "--format=csv,noheader,nounits",
        ],
        timeout=10,
    )
    if output is None:
        return []
    devices = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) >= 6:
            devices.append(
                {
                    "index": number(fields[0]),
                    "utilization_percent": number(fields[1]),
                    "memory_used_mib": number(fields[2]),
                    "memory_total_mib": number(fields[3]),
                    "power_watts": number(fields[4]),
                    "temperature_c": number(fields[5]),
                }
            )
    return devices


def summarize_resources(samples: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "sample_count": len(samples),
        "system": {},
        "processes": {},
        "gpus": {},
    }
    summary["system"] = {
        "cpu_percent": summarize_numbers(
            sample.get("system_cpu_percent") for sample in samples
        ),
        "memory_used_bytes": summarize_numbers(
            sample.get("system_memory_used_bytes") for sample in samples
        ),
    }
    process_names = sorted(
        {name for sample in samples for name in sample.get("processes", {})}
    )
    for name in process_names:
        process_samples = [
            sample.get("processes", {}).get(name, {}) for sample in samples
        ]
        summary["processes"][name] = {
            "cpu_percent": summarize_numbers(
                sample.get("cpu_percent") for sample in process_samples
            ),
            "rss_bytes": summarize_numbers(
                sample.get("rss_bytes") for sample in process_samples
            ),
        }
    gpu_indexes = sorted(
        {
            int(gpu["index"])
            for sample in samples
            for gpu in sample.get("gpus", [])
            if gpu.get("index") is not None
        }
    )
    for index in gpu_indexes:
        gpu_samples = [
            gpu
            for sample in samples
            for gpu in sample.get("gpus", [])
            if gpu.get("index") == index
        ]
        summary["gpus"][str(index)] = {
            key: summarize_numbers(gpu.get(key) for gpu in gpu_samples)
            for key in (
                "utilization_percent",
                "memory_used_mib",
                "power_watts",
                "temperature_c",
            )
        }
    return summary


def summarize_numbers(values: Any) -> dict[str, float] | None:
    numbers = [float(value) for value in values if isinstance(value, int | float)]
    if not numbers:
        return None
    ordered = sorted(numbers)
    return {
        "mean": round(statistics.fmean(ordered), 3),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
        "p95": round(percentile(ordered, 95), 3),
    }


def percentile(ordered: list[float], percent: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def run_text(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 5,
) -> str | None:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def number(value: str) -> int | float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() else parsed


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
