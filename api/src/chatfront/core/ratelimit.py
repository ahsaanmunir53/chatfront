"""In-memory rate limits for public endpoints. One Render instance, so memory is enough."""

from __future__ import annotations

import time
from collections import deque

from chatfront.core.errors import RateLimited

WINDOW_SECONDS = 60.0


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = {}

    def check(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        window = self.hits.setdefault(key, deque())
        while window and window[0] <= now - WINDOW_SECONDS:
            window.popleft()
        if len(window) >= self.per_minute:
            raise RateLimited
        window.append(now)
        if len(self.hits) > 10_000:
            self._prune(now)

    def _prune(self, now: float) -> None:
        stale = [k for k, w in self.hits.items() if not w or w[-1] <= now - WINDOW_SECONDS]
        for key in stale:
            del self.hits[key]
