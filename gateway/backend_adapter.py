"""Optional OpenAI-compatible adapter for vLLM and SGLang-style backends."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import httpx

from gateway.mock_inference import run_mock_inference

DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_COMPLETION_TOKENS = 256
DEFAULT_MAX_BACKEND_CONNECTIONS = 256
DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 128

_http_client: httpx.Client | None = None
_http_client_lock = threading.Lock()


class BackendAdapterError(RuntimeError):
    """Raised when an external inference backend cannot return a valid result."""


def backend_http_client() -> httpx.Client:
    """Return the process-wide connection pool used by the streaming proxy."""
    global _http_client
    if _http_client is not None:
        return _http_client

    with _http_client_lock:
        if _http_client is None:
            _http_client = httpx.Client(
                limits=httpx.Limits(
                    max_connections=_positive_int_env(
                        "INFERENCE_BACKEND_MAX_CONNECTIONS",
                        DEFAULT_MAX_BACKEND_CONNECTIONS,
                    ),
                    max_keepalive_connections=_positive_int_env(
                        "INFERENCE_BACKEND_MAX_KEEPALIVE_CONNECTIONS",
                        DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
                    ),
                ),
                trust_env=_env_bool("INFERENCE_BACKEND_TRUST_ENV", False),
            )
    return _http_client


def open_completion_response(
    payload: dict[str, object],
    *,
    endpoint: str | None = None,
    api_key: str | None = None,
    timeout_seconds: float | None = None,
    request_id: str | None = None,
    client: httpx.Client | None = None,
) -> httpx.Response:
    """Open a pooled OpenAI-compatible completion response for passthrough.

    The caller owns the returned response and must close it. The backend URL is
    configuration-only; request data cannot select an arbitrary upstream host.
    """
    configured_endpoint = (
        endpoint
        if endpoint is not None
        else os.getenv("INFERENCE_BACKEND_COMPLETIONS_URL", "")
    )
    if not configured_endpoint.strip():
        raise BackendAdapterError("inference backend is not configured")

    timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else _backend_timeout_seconds()
    )
    if timeout <= 0:
        raise BackendAdapterError("backend timeout must be positive")

    headers = {
        "Accept": "text/event-stream" if payload.get("stream") else "application/json",
        "Content-Type": "application/json",
    }
    configured_api_key = (
        api_key if api_key is not None else os.getenv("INFERENCE_BACKEND_API_KEY")
    )
    if configured_api_key:
        headers["Authorization"] = f"Bearer {configured_api_key}"
    if request_id:
        headers["X-Request-Id"] = request_id

    backend_client = client or backend_http_client()
    request = backend_client.build_request(
        "POST",
        completion_url(configured_endpoint),
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    try:
        response = backend_client.send(request, stream=True)
    except httpx.HTTPError as error:
        raise BackendAdapterError("inference backend request failed") from error

    if response.status_code < 200 or response.status_code >= 300:
        response.close()
        raise BackendAdapterError("inference backend returned a non-success status")
    return response


def completion_url(endpoint: str) -> str:
    """Normalize a base URL or completion URL without adding duplicate paths."""
    normalized = endpoint.strip().rstrip("/")
    if not normalized:
        raise BackendAdapterError("backend endpoint must not be empty")
    if normalized.endswith("/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/completions"
    return f"{normalized}/v1/completions"


def run_openai_compatible_inference(
    model_id: str,
    user_input: str,
    *,
    endpoint: str,
    api_key: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, object]:
    """Call a vLLM/SGLang-style completion endpoint without logging payloads."""
    if timeout_seconds <= 0:
        raise BackendAdapterError("backend timeout must be positive")

    payload = {
        "model": model_id,
        "prompt": user_input,
        "max_tokens": MAX_COMPLETION_TOKENS,
        "stream": False,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = Request(
        completion_url(endpoint),
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    try:
        with opener(request, timeout=timeout_seconds) as response:
            raw_payload = response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise BackendAdapterError("inference backend request failed") from error

    try:
        response_payload = json.loads(raw_payload)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackendAdapterError("inference backend returned invalid JSON") from error

    output, response_model = extract_completion(response_payload)
    usage = response_payload.get("usage")
    if usage is not None and not isinstance(usage, dict):
        raise BackendAdapterError("inference backend returned invalid usage metadata")

    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    result: dict[str, object] = {
        "model_id": response_model or model_id,
        "output": output,
        "latency_ms": latency_ms,
        "backend": "openai-compatible",
    }
    if usage:
        result["usage"] = {
            key: int(value)
            for key, value in usage.items()
            if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
            and isinstance(value, int)
            and not isinstance(value, bool)
        }
    return result


def extract_completion(response_payload: Any) -> tuple[str, str | None]:
    """Extract text from completion or chat-completion response shapes."""
    if not isinstance(response_payload, dict):
        raise BackendAdapterError("inference backend response must be an object")
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise BackendAdapterError("inference backend response has no choices")

    choice = choices[0]
    output = choice.get("text")
    if output is None:
        message = choice.get("message")
        output = message.get("content") if isinstance(message, dict) else None
    if not isinstance(output, str):
        raise BackendAdapterError("inference backend response has no text content")

    model = response_payload.get("model")
    return output, model if isinstance(model, str) and model else None


def run_configured_inference(model_id: str, user_input: str) -> dict[str, object]:
    """Use the external adapter only when explicitly configured; otherwise use the mock."""
    endpoint = os.getenv("INFERENCE_BACKEND_COMPLETIONS_URL", "").strip()
    if not endpoint:
        return run_mock_inference(model_id, user_input)

    timeout_ms = os.getenv("INFERENCE_BACKEND_TIMEOUT_MS", "5000")
    try:
        timeout_seconds = float(timeout_ms) / 1000
    except ValueError as error:
        raise BackendAdapterError("backend timeout must be numeric") from error

    return run_openai_compatible_inference(
        model_id,
        user_input,
        endpoint=endpoint,
        api_key=os.getenv("INFERENCE_BACKEND_API_KEY"),
        timeout_seconds=timeout_seconds,
    )


def _backend_timeout_seconds() -> float:
    timeout_ms = os.getenv("INFERENCE_BACKEND_TIMEOUT_MS", "5000")
    try:
        return float(timeout_ms) / 1000
    except ValueError as error:
        raise BackendAdapterError("backend timeout must be numeric") from error


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise BackendAdapterError(f"{name} must be an integer") from error
    if value < 1:
        raise BackendAdapterError(f"{name} must be positive")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
