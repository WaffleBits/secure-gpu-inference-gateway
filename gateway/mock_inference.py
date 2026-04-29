from __future__ import annotations

import hashlib
import time


def run_mock_inference(model_id: str, user_input: str) -> dict[str, object]:
    start = time.perf_counter()
    fingerprint = hashlib.sha256(f"{model_id}:{user_input}".encode("utf-8")).hexdigest()[:12]
    time.sleep(0.025)
    latency_ms = round((time.perf_counter() - start) * 1000, 3)

    return {
        "model_id": model_id,
        "output": f"synthetic response {fingerprint}",
        "latency_ms": latency_ms,
        "backend": "mock-gpu",
    }

