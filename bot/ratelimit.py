"""按用户的滑动窗口限流 —— 给 /raw 防刷用。"""
from __future__ import annotations

import time


class RateLimiter:
    """每 key 在 window 秒内最多 limit 次。limit <= 0 表示不限。"""

    def __init__(self, limit: int, window: float = 60.0) -> None:
        self._limit = limit
        self._window = window
        self._hits: dict[str, list[float]] = {}

    @property
    def enabled(self) -> bool:
        return self._limit > 0

    def check(self, key: str) -> tuple[bool, float]:
        """记一次；返回 (是否放行, 还需等待的秒数)。"""
        if not self.enabled:
            return True, 0.0

        now = time.monotonic()
        hits = self._hits.setdefault(key, [])
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.pop(0)

        if len(hits) >= self._limit:
            return False, max(0.0, self._window - (now - hits[0]))

        hits.append(now)
        return True, 0.0
