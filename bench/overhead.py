"""Measure the latency the gateway adds in front of a backend that does no work.

The vLLM benchmark compares whole inference requests, where GPU time hides the
gateway. Here the backend streams a fixed reply immediately, so the difference
between the direct and gateway paths is the cost of the control path: JWT
verification, policy, limiters, audit, metrics, trace export and proxying.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import platform
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ISSUER = "overhead-benchmark"
AUDIENCE = "secure-gpu-inference-gateway"
MODEL = "benchmark-echo"
STREAM_END = b"\r\n0\r\n\r\n"

# Client, backend and gateway stay on separate SMT pairs when --pin is set.
CLIENT_CPUS = {0, 1}
BACKEND_CPUS = {2, 3}
GATEWAY_CPUS = {4, 5, 6, 7}


def percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        raise ValueError("no samples")
    rank = max(1, round(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


def summarize_run(
    latencies_s: list[float], elapsed_s: float, failures: int
) -> dict[str, float | int]:
    ordered = sorted(latencies_s)
    return {
        "requests": len(ordered),
        "failures": failures,
        "requests_per_second": len(ordered) / elapsed_s,
        "p50_us": percentile(ordered, 0.50) * 1e6,
        "p95_us": percentile(ordered, 0.95) * 1e6,
        "p99_us": percentile(ordered, 0.99) * 1e6,
    }


def paired_added_latency(
    direct_runs: list[dict[str, Any]], gateway_runs: list[dict[str, Any]]
) -> dict[str, dict[str, float]]:
    """Subtract each repetition's direct run from its gateway run."""
    if len(direct_runs) != len(gateway_runs) or not direct_runs:
        raise ValueError("direct and gateway runs must pair one to one")
    added = {}
    for metric in ("p50_us", "p95_us", "p99_us"):
        deltas = [
            gateway[metric] - direct[metric]
            for direct, gateway in zip(direct_runs, gateway_runs)
        ]
        added[metric] = {
            "median": statistics.median(deltas),
            "min": min(deltas),
            "max": max(deltas),
        }
    return added


def make_jwt(secret: str, ttl_seconds: int) -> str:
    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "overhead-benchmark-client",
        "roles": ["analyst"],
        "iat": now,
        "exp": now + ttl_seconds,
    }
    header = encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    payload = encode(json.dumps(claims).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256)
    return f"{header}.{payload}.{encode(signature.digest())}"


def build_request(token: str, prompt_words: int, max_tokens: int) -> bytes:
    body = json.dumps(
        {
            "model": MODEL,
            "prompt": "word " * prompt_words,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    ).encode("utf-8")
    head = (
        "POST /v1/completions HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: application/json\r\n"
        f"Authorization: Bearer {token}\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    )
    return head.encode("ascii") + body


def stream_chunks(tokens: int) -> list[bytes]:
    events = [
        b'data: {"choices":[{"index":0,"text":"x"}]}\n\n' for _ in range(tokens)
    ]
    events.append(
        b'data: {"usage":{"prompt_tokens":1,"completion_tokens":%d}}\n\n' % tokens
    )
    events.append(b"data: [DONE]\n\n")
    return [b"%x\r\n%s\r\n" % (len(event), event) for event in events]


async def serve_null_backend(port: int, tokens: int) -> None:
    chunks = stream_chunks(tokens)
    head = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/event-stream\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n"
    )

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                headers = await reader.readuntil(b"\r\n\r\n")
                length = 0
                for line in headers.lower().split(b"\r\n"):
                    if line.startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])
                await reader.readexactly(length)
                writer.write(head)
                for chunk in chunks:
                    writer.write(chunk)
                    await writer.drain()
                writer.write(b"0\r\n\r\n")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    async with server:
        await server.serve_forever()


async def drive(
    port: int, request: bytes, concurrency: int, total: int
) -> tuple[list[float], float, int]:
    """Closed-loop load: each connection sends its next request after a reply."""
    remaining = total
    latencies: list[float] = []
    failures = 0

    async def worker() -> None:
        nonlocal remaining, failures
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            while remaining > 0:
                remaining -= 1
                started = time.perf_counter()
                writer.write(request)
                await writer.drain()
                headers = await reader.readuntil(b"\r\n\r\n")
                if b" 200 " not in headers[:20]:
                    failures += 1
                    raise RuntimeError(headers[:200].decode("latin-1"))
                await reader.readuntil(STREAM_END)
                latencies.append(time.perf_counter() - started)
        finally:
            writer.close()

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return latencies, time.perf_counter() - started, failures


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for_port(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process exited with {process.returncode}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"port {port} did not open")


def start(
    command: list[str], env: dict[str, str], cpus: set[int] | None
) -> subprocess.Popen[bytes]:
    def pin() -> None:
        os.sched_setaffinity(0, cpus)

    return subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=pin if cpus else None,
    )


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def wait_for_lines(path: Path, expected: int) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        lines = count_lines(path)
        if lines >= expected:
            return lines
        time.sleep(0.05)
    return count_lines(path)


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    pin = args.pin and hasattr(os, "sched_setaffinity")
    if pin:
        os.sched_setaffinity(0, CLIENT_CPUS)

    secret = secrets.token_urlsafe(32)
    backend_port = free_port()
    gateway_port = free_port()
    request = build_request(
        make_jwt(secret, 86400), args.prompt_words, args.tokens
    )
    results: dict[str, Any] = {"conditions": []}

    with tempfile.TemporaryDirectory(prefix="gateway-overhead-") as tmp:
        audit_path = Path(tmp) / "audit.jsonl"
        trace_path = Path(tmp) / "traces.jsonl"
        env = {
            **os.environ,
            "ALLOW_DEMO_PRINCIPALS": "false",
            "OIDC_ISSUER": ISSUER,
            "OIDC_AUDIENCE": AUDIENCE,
            "OIDC_JWT_HS256_SECRET": secret,
            "AUDIT_LOG_PATH": str(audit_path),
            "TRACE_EXPORT_PATH": str(trace_path),
            "INFERENCE_BACKEND_COMPLETIONS_URL": (
                f"http://127.0.0.1:{backend_port}/v1"
            ),
            "BENCHMARK_REQUESTS_PER_MINUTE": "1000000000",
            "BENCHMARK_INPUT_TOKENS_PER_MINUTE": "1000000000000",
            "RATE_LIMIT_BACKEND": "redis" if args.redis_url else "memory",
        }
        if args.redis_url:
            env["REDIS_URL"] = args.redis_url
            env["REDIS_KEY_PREFIX"] = f"overhead-{secrets.token_hex(4)}"

        backend = start(
            [
                sys.executable,
                "-m",
                "bench.overhead",
                "--serve-null",
                str(backend_port),
                "--tokens",
                str(args.tokens),
            ],
            env,
            BACKEND_CPUS if pin else None,
        )
        gateway = start(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "gateway.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(gateway_port),
                "--workers",
                "1",
                "--no-access-log",
                "--log-level",
                "warning",
            ],
            env,
            GATEWAY_CPUS if pin else None,
        )
        try:
            wait_for_port(backend_port, backend)
            wait_for_port(gateway_port, gateway)
            ports = {"direct": backend_port, "gateway": gateway_port}
            gateway_requests = 0
            for concurrency in args.concurrency:
                runs: dict[str, list[dict[str, Any]]] = {
                    "direct": [],
                    "gateway": [],
                }
                for repetition in range(args.repetitions):
                    order = ["direct", "gateway"]
                    if repetition % 2:
                        order.reverse()
                    for path in order:
                        asyncio.run(
                            drive(ports[path], request, concurrency, args.warmup)
                        )
                        latencies, elapsed, failures = asyncio.run(
                            drive(ports[path], request, concurrency, args.requests)
                        )
                        if path == "gateway":
                            gateway_requests += args.warmup + args.requests
                        runs[path].append(
                            {
                                "repetition": repetition,
                                **summarize_run(latencies, elapsed, failures),
                            }
                        )
                results["conditions"].append(
                    {
                        "concurrency": concurrency,
                        "direct": runs["direct"],
                        "gateway": runs["gateway"],
                        "added": paired_added_latency(
                            runs["direct"], runs["gateway"]
                        ),
                    }
                )
            audit_events = wait_for_lines(audit_path, gateway_requests)
            trace_spans = wait_for_lines(trace_path, gateway_requests)
            if audit_events != gateway_requests or trace_spans != gateway_requests:
                raise RuntimeError(
                    f"expected {gateway_requests} audit events and trace spans, "
                    f"found {audit_events} and {trace_spans}"
                )
            results["control_path_check"] = {
                "gateway_requests": gateway_requests,
                "audit_events": audit_events,
                "trace_spans": trace_spans,
            }
        finally:
            gateway.terminate()
            backend.terminate()
            gateway.wait(timeout=10)
            backend.wait(timeout=10)

    results["configuration"] = {
        "limiter": "redis" if args.redis_url else "memory",
        "auth": "HS256 JWT",
        "uvicorn_workers": 1,
        "tls": False,
        "pinned": bool(pin),
        "requests_per_run": args.requests,
        "warmup_per_run": args.warmup,
        "repetitions": args.repetitions,
        "prompt_words": args.prompt_words,
        "stream_tokens": args.tokens,
    }
    results["environment"] = capture_environment()
    return results


def capture_environment() -> dict[str, str]:
    cpu = platform.processor()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return {
        "commit": commit,
        "cpu": cpu,
        "kernel": platform.release(),
        "python": platform.python_version(),
    }


def render_report(results: dict[str, Any]) -> str:
    config = results["configuration"]
    environment = results["environment"]
    check = results["control_path_check"]
    lines = [
        "# Gateway overhead against a null backend",
        "",
        f"Commit `{environment['commit'][:7]}` on {environment['cpu']}, "
        f"kernel {environment['kernel']}, Python {environment['python']}.",
        "",
        f"{config['limiter'].capitalize()} limiter, {config['auth']}, "
        f"{config['uvicorn_workers']} uvicorn worker, no TLS, loopback. "
        f"{config['repetitions']} paired repetitions of "
        f"{config['requests_per_run']} requests, each streaming "
        f"{config['stream_tokens']} tokens.",
        "",
        "Added latency is gateway minus direct within a repetition. The table "
        "shows the median across repetitions and the range in brackets, in "
        "microseconds.",
        "",
        "| Concurrency | Direct p50 | Gateway p50 | Added p50 | Added p95 "
        "| Added p99 | Gateway req/s |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def cell(value: dict[str, float]) -> str:
        return f"{value['median']:.0f} [{value['min']:.0f}, {value['max']:.0f}]"

    for condition in results["conditions"]:
        direct_p50 = statistics.median(r["p50_us"] for r in condition["direct"])
        gateway_p50 = statistics.median(r["p50_us"] for r in condition["gateway"])
        rps = statistics.median(
            r["requests_per_second"] for r in condition["gateway"]
        )
        added = condition["added"]
        lines.append(
            f"| {condition['concurrency']} | {direct_p50:.0f} | {gateway_p50:.0f} "
            f"| {cell(added['p50_us'])} | {cell(added['p95_us'])} "
            f"| {cell(added['p99_us'])} | {rps:.0f} |"
        )
    lines += [
        "",
        f"All {check['gateway_requests']} gateway requests produced an audit "
        "event and a trace span, so the control path ran for every request.",
        "",
        "Above concurrency 1 the added latency includes queueing on one Python "
        "worker, so those rows show saturation, not per-request cost. This is "
        "not a production capacity figure.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-null", type=int, metavar="PORT")
    parser.add_argument("--tokens", type=int, default=16)
    parser.add_argument("--prompt-words", type=int, default=64)
    parser.add_argument("--requests", type=int, default=5000)
    parser.add_argument("--warmup", type=int, default=500)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 8, 32])
    parser.add_argument("--redis-url")
    parser.add_argument("--pin", action="store_true")
    parser.add_argument("--label", default="local")
    args = parser.parse_args()

    if args.serve_null is not None:
        asyncio.run(serve_null_backend(args.serve_null, args.tokens))
        return

    results = run_benchmark(args)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "bench" / "results" / f"{stamp}-null-backend-{args.label}"
    output.mkdir(parents=True)
    (output / "results.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    report = render_report(results)
    (output / "report.md").write_text(report, encoding="utf-8")
    print(report)
    print(output)


if __name__ == "__main__":
    main()
