from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol


REDIS_FIXED_WINDOW_LUA = """local current = redis.call(\"INCRBY\", KEYS[1], ARGV[1])
if current == tonumber(ARGV[1]) then
  redis.call(\"PEXPIRE\", KEYS[1], ARGV[2])
end
if current > tonumber(ARGV[3]) then
  return {0, current, tonumber(ARGV[3])}
end
return {1, current, tonumber(ARGV[3])}
"""


class RedisScriptClient(Protocol):
    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any:
        ...


@dataclass
class Window:
    started_at: float
    count: int = 0


class FixedWindowRateLimiter:
    def __init__(self, window_seconds: int = 60) -> None:
        self.window_seconds = window_seconds
        self.windows: dict[tuple[str, str], Window] = {}

    def allow(self, principal_id: str, model_id: str, limit: int) -> bool:
        now = time.time()
        key = (principal_id, model_id)
        window = self.windows.get(key)

        if window is None or now - window.started_at >= self.window_seconds:
            self.windows[key] = Window(started_at=now, count=1)
            return True

        if window.count >= limit:
            return False

        window.count += 1
        return True


class FixedWindowTokenBudgetLimiter:
    def __init__(self, window_seconds: int = 60) -> None:
        self.window_seconds = window_seconds
        self.windows: dict[tuple[str, str], Window] = {}

    def allow(self, principal_id: str, model_id: str, cost: int, limit: int) -> bool:
        if cost < 1:
            raise ValueError("token cost must be positive")
        if limit < 1:
            raise ValueError("token limit must be positive")

        now = time.time()
        key = (principal_id, model_id)
        window = self.windows.get(key)

        if window is None or now - window.started_at >= self.window_seconds:
            if cost > limit:
                return False
            self.windows[key] = Window(started_at=now, count=cost)
            return True

        if window.count + cost > limit:
            return False

        window.count += cost
        return True


class _RedisFixedWindowLimiter:
    def __init__(
        self,
        client: RedisScriptClient,
        *,
        budget_type: str,
        window_seconds: int = 60,
        key_prefix: str = "sgig",
    ) -> None:
        if window_seconds < 1:
            raise ValueError("window must be positive")
        if not key_prefix:
            raise ValueError("Redis key prefix must not be empty")
        self.client = client
        self.budget_type = budget_type
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix

    def _allow(self, principal_id: str, model_id: str, cost: int, limit: int) -> bool:
        if cost < 1:
            raise ValueError("cost must be positive")
        if limit < 1:
            raise ValueError("limit must be positive")

        window_epoch = int(time.time() // self.window_seconds)
        caller_hash = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()[:24]
        key = (
            f"{self.key_prefix}:{model_id}:{self.budget_type}:"
            f"{caller_hash}:{window_epoch}"
        )
        decision = self.client.eval(
            REDIS_FIXED_WINDOW_LUA,
            1,
            key,
            str(cost),
            str(self.window_seconds * 1000),
            str(limit),
        )
        try:
            return int(decision[0]) == 1
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Redis limiter returned an invalid decision") from exc


class RedisFixedWindowRateLimiter(_RedisFixedWindowLimiter):
    def __init__(
        self,
        client: RedisScriptClient,
        window_seconds: int = 60,
        key_prefix: str = "sgig",
    ) -> None:
        super().__init__(
            client,
            budget_type="requests",
            window_seconds=window_seconds,
            key_prefix=key_prefix,
        )

    def allow(self, principal_id: str, model_id: str, limit: int) -> bool:
        return self._allow(principal_id, model_id, 1, limit)


class RedisFixedWindowTokenBudgetLimiter(_RedisFixedWindowLimiter):
    def __init__(
        self,
        client: RedisScriptClient,
        window_seconds: int = 60,
        key_prefix: str = "sgig",
    ) -> None:
        super().__init__(
            client,
            budget_type="input_tokens",
            window_seconds=window_seconds,
            key_prefix=key_prefix,
        )

    def allow(self, principal_id: str, model_id: str, cost: int, limit: int) -> bool:
        return self._allow(principal_id, model_id, cost, limit)


def build_limiters_from_env() -> tuple[object, object]:
    backend = os.getenv("RATE_LIMIT_BACKEND", "memory").strip().lower()
    if backend == "memory":
        return FixedWindowRateLimiter(), FixedWindowTokenBudgetLimiter()
    if backend != "redis":
        raise ValueError("RATE_LIMIT_BACKEND must be memory or redis")

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL is required when RATE_LIMIT_BACKEND=redis")
    try:
        import redis
    except ImportError as exc:
        raise RuntimeError(
            "Redis mode requires the optional redis dependency; install requirements-redis.txt"
        ) from exc

    socket_timeout = float(os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", "1.0"))
    client = redis.Redis.from_url(
        redis_url,
        decode_responses=False,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_timeout,
    )
    client.ping()
    key_prefix = os.getenv("REDIS_KEY_PREFIX", "sgig")
    return (
        RedisFixedWindowRateLimiter(client, key_prefix=key_prefix),
        RedisFixedWindowTokenBudgetLimiter(client, key_prefix=key_prefix),
    )
