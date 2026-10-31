from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path

from gateway.models import AuditEvent


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

