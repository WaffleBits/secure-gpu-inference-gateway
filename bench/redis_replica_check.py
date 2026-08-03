from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


def send_request(gateway_url: str, principal: str, request_index: int) -> int:
    request = urllib.request.Request(
        gateway_url.rstrip("/") + "/v1/infer/benchmark-echo",
        data=json.dumps({"input": f"replica-check-{request_index}"}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Principal-Id": principal,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as error:
        error.read()
        return error.code


def run_check(
    gateway_urls: list[str],
    *,
    request_limit: int,
    window_seconds: float,
    total_requests: int,
    request_fn: Callable[[str, str, int], int] = send_request,
) -> dict[str, Any]:
    if len(gateway_urls) < 2:
        raise ValueError("at least two gateway URLs are required")
    if total_requests <= request_limit:
        raise ValueError("total_requests must exceed request_limit")

    started = time.perf_counter()
    captured_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with ThreadPoolExecutor(max_workers=min(total_requests, 64)) as executor:
        statuses = list(
            executor.map(
                lambda index: request_fn(
                    gateway_urls[index % len(gateway_urls)],
                    "analyst-1",
                    index,
                ),
                range(total_requests),
            )
        )
    allowed = statuses.count(200)
    limited = statuses.count(429)
    unexpected = [status for status in statuses if status not in {200, 429}]

    isolated_status = request_fn(gateway_urls[0], "security-1", total_requests + 1)
    expiration_wait_seconds = window_seconds + max(1.0, window_seconds * 0.1)
    time.sleep(expiration_wait_seconds)
    expired_status = request_fn(gateway_urls[1], "analyst-1", total_requests + 2)
    checks = {
        "atomic_limit": allowed == request_limit,
        "all_excess_limited": limited == total_requests - request_limit,
        "no_unexpected_status": not unexpected,
        "caller_isolation": isolated_status == 200,
        "window_expiration": expired_status == 200,
    }
    return {
        "schema_version": 1,
        "captured_at": captured_at,
        "duration_seconds": round(time.perf_counter() - started, 6),
        "status": "pass" if all(checks.values()) else "fail",
        "gateway_replicas": len(gateway_urls),
        "request_limit": request_limit,
        "window_seconds": window_seconds,
        "expiration_wait_seconds": expiration_wait_seconds,
        "total_requests": total_requests,
        "allowed": allowed,
        "limited": limited,
        "unexpected_statuses": unexpected,
        "caller_isolation_status": isolated_status,
        "post_expiration_status": expired_status,
        "checks": checks,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify one Redis request budget across multiple gateway replicas."
    )
    parser.add_argument("--gateway-url", action="append", required=True)
    parser.add_argument("--request-limit", type=int, required=True)
    parser.add_argument("--window-seconds", type=float, required=True)
    parser.add_argument("--total-requests", type=int, default=30)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_check(
        args.gateway_url,
        request_limit=args.request_limit,
        window_seconds=args.window_seconds,
        total_requests=args.total_requests,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
