from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from gateway.models import AuditEvent


class DenialAuditBudget:
    """Cap how many denials from unauthenticated callers reach the audit log.

    Denials need no credentials, so without a cap anyone can fill the disk.
    Authenticated denials bypass this and are always written.
    """

    def __init__(
        self,
        limit_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit_per_minute = limit_per_minute
        self.clock = clock
        self._window_started = clock()
        self._count = 0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = self.clock()
            if now - self._window_started >= 60:
                self._window_started = now
                self._count = 0
            if self._count >= self.limit_per_minute:
                return False
            self._count += 1
            return True


class JsonlAuditSink:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or os.getenv("AUDIT_LOG_PATH", "audit.log"))
        self._lock = threading.Lock()

    def write(self, event: AuditEvent) -> None:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **asdict(event),
        }
        line = json.dumps(payload, sort_keys=True) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)

