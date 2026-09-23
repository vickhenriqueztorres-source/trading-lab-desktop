"""In-memory rate limiter for Trading Lab License Server."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Protocol


class RateLimiterProtocol(Protocol):
    """Protocol for rate limiting implementations."""

    def check(self, key: str, max_requests: int, window_seconds: float) -> tuple[bool, int]:
        """Check if request for key is allowed.

        Returns (is_allowed, retry_after_seconds).
        """
        ...


class InMemoryRateLimiter:
    """Sliding-window in-memory rate limiter suitable for single-instance service."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._history: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key: str, max_requests: int, window_seconds: float) -> tuple[bool, int]:
        if max_requests <= 0 or window_seconds <= 0:
            return True, 0

        now = self._clock()
        threshold = now - window_seconds

        with self._lock:
            timestamps = self._history[key]
            # Prune expired entries
            valid = [ts for ts in timestamps if ts > threshold]
            if len(valid) >= max_requests:
                earliest = min(valid)
                retry_after = max(1, int(earliest + window_seconds - now))
                self._history[key] = valid
                return False, retry_after

            valid.append(now)
            self._history[key] = valid
            return True, 0

    def reset(self, key: str | None = None) -> None:
        """Reset rate limiter state (useful in tests)."""
        with self._lock:
            if key is None:
                self._history.clear()
            else:
                self._history.pop(key, None)


_GLOBAL_RATE_LIMITER = InMemoryRateLimiter()


def get_rate_limiter() -> InMemoryRateLimiter:
    return _GLOBAL_RATE_LIMITER
