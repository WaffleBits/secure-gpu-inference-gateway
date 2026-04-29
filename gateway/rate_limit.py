from __future__ import annotations

import time
from dataclasses import dataclass


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

