from __future__ import annotations

import math


def estimate_input_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 1
    return max(1, math.ceil(len(stripped) / 4))
