"""Thread-safe, monotonic circuit breakers with bounded sliding windows."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Any


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class WindowMode(StrEnum):
    COUNT = "COUNT"
    TIME = "TIME"


@dataclass(frozen=True, slots=True)
class CircuitStats:
    state: CircuitState
    failures: int
    successes: int
    samples: int
    opened_at: float | None
    next_probe_at: float | None


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        window_size: int = 20,
        window_seconds: float = 60.0,
        window_mode: WindowMode | str = WindowMode.COUNT,
        minimum_samples: int = 1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= failure_threshold <= 10_000:
            raise ValueError("failure_threshold must be between 1 and 10000")
        if not 1 <= minimum_samples <= window_size <= 100_000:
            raise ValueError("window sample limits are invalid")
        if recovery_timeout <= 0 or window_seconds <= 0:
            raise ValueError("circuit durations must be positive")
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.window_size = window_size
        self.window_seconds = window_seconds
        self.window_mode = WindowMode(window_mode)
        self.minimum_samples = minimum_samples
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._opened_at: float | None = None
        self._half_open_probe_active = False
        self._events: deque[tuple[float, bool]] = deque(maxlen=window_size)
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._advance_open_state(self._clock())
            return self._state

    def can_execute(self) -> bool:
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._advance_open_state(now)
            if self._state is CircuitState.CLOSED:
                return True
            if self._state is CircuitState.HALF_OPEN and not self._half_open_probe_active:
                self._half_open_probe_active = True
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            now = self._clock()
            if self._state is CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._opened_at = None
                self._half_open_probe_active = False
                self._events.clear()
            self._events.append((now, True))
            self._prune(now)

    def record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            if self._state is CircuitState.HALF_OPEN:
                self._open(now)
                return
            self._events.append((now, False))
            self._prune(now)
            failures = sum(not success for _timestamp, success in self._events)
            if len(self._events) >= self.minimum_samples and failures >= self.failure_threshold:
                self._open(now)

    def get_stats(self) -> CircuitStats:
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._advance_open_state(now)
            failures = sum(not success for _timestamp, success in self._events)
            successes = len(self._events) - failures
            next_probe = (
                None if self._opened_at is None else self._opened_at + self.recovery_timeout
            )
            return CircuitStats(
                self._state,
                failures,
                successes,
                len(self._events),
                self._opened_at,
                next_probe,
            )

    def _open(self, now: float) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = now
        self._half_open_probe_active = False

    def _advance_open_state(self, now: float) -> None:
        if (
            self._state is CircuitState.OPEN
            and self._opened_at is not None
            and now - self._opened_at >= self.recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            self._half_open_probe_active = False

    def _prune(self, now: float) -> None:
        if self.window_mode is WindowMode.TIME:
            cutoff = now - self.window_seconds
            while self._events and self._events[0][0] < cutoff:
                self._events.popleft()


class ConnectionBreaker(CircuitBreaker):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("failure_threshold", 3)
        kwargs.setdefault("recovery_timeout", 300.0)
        super().__init__(**kwargs)


class MarketDataBreaker(CircuitBreaker):
    pass


class OrderSubmitBreaker(CircuitBreaker):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("failure_threshold", 2)
        kwargs.setdefault("recovery_timeout", 60.0)
        super().__init__(**kwargs)


class AccountQueryBreaker(CircuitBreaker):
    pass


class AuthBreaker(CircuitBreaker):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("failure_threshold", 3)
        kwargs.setdefault("recovery_timeout", 120.0)
        super().__init__(**kwargs)


__all__ = [
    "AccountQueryBreaker",
    "AuthBreaker",
    "CircuitBreaker",
    "CircuitState",
    "CircuitStats",
    "ConnectionBreaker",
    "MarketDataBreaker",
    "OrderSubmitBreaker",
    "WindowMode",
]
