from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if body is not None else {}),
            **({"X-Principal-Id": "analyst-1"} if payload is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def exercise(allow_demo: bool | None) -> tuple[int, dict[str, Any]]:
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="gateway-demo-auth-") as tempdir:
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": tempdir,
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "AUDIT_LOG_PATH": str(Path(tempdir) / "audit.jsonl"),
        }
        if allow_demo is not None:
            env["ALLOW_DEMO_PRINCIPALS"] = "true" if allow_demo else "false"

        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "gateway.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("gateway service exited before becoming ready")
                try:
                    status, _ = request_json(f"http://127.0.0.1:{port}/health")
                    if status == 200:
                        break
                except (OSError, TimeoutError, urllib.error.URLError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("gateway service did not become ready within 15 seconds")

            return request_json(
                f"http://127.0.0.1:{port}/v1/infer/benchmark-echo",
                method="POST",
                payload={"input": "synthetic auth-default fixture", "reason": "test only"},
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    status, body = exercise(None)
    if status != 403 or "demo principals disabled" not in json.dumps(body):
        raise AssertionError(
            f"unset ALLOW_DEMO_PRINCIPALS must deny demo identity (HTTP 403); got HTTP {status}"
        )
    print("HTTP fixture: unset ALLOW_DEMO_PRINCIPALS denies demo identity (403).")

    status, body = exercise(True)
    if status != 200 or body.get("backend") != "mock-gpu":
        raise AssertionError(
            f"explicit local opt-in must reach the mock backend (HTTP 200); got HTTP {status}"
        )
    print("HTTP fixture: explicit ALLOW_DEMO_PRINCIPALS=true reaches the mock backend (200).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
