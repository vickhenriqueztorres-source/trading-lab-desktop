"""Small supervisor integration client with crash-loop detection."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SupervisorRegistration:
    worker_id: str
    registered_at: float


class SupervisorClient:
    def __init__(
        self,
        worker_id: str,
        *,
        crash_window_seconds: float = 300.0,
        max_crashes: int = 5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if crash_window_seconds <= 0 or max_crashes <= 0:
            raise ValueError("supervisor limits must be positive")
        self.worker_id = worker_id
        self.crash_window_seconds = crash_window_seconds
        self.max_crashes = max_crashes
        self._clock = clock
        self.registration: SupervisorRegistration | None = None
        self._crashes: deque[float] = deque()
        self.last_health: dict[str, Any] | None = None

    def register(self) -> SupervisorRegistration:
        self.registration = SupervisorRegistration(self.worker_id, self._clock())
        return self.registration

    def heartbeat(self, health: dict[str, Any]) -> bool:
        if self.registration is None:
            return False
        self.last_health = dict(health)
        return bool(health.get("liveness")) and not self.crash_loop_detected()

    def deregister(self) -> None:
        self.registration = None
        self.last_health = None

    def record_crash(self) -> None:
        now = self._clock()
        self._prune(now)
        self._crashes.append(now)

    def crash_loop_detected(self) -> bool:
        self._prune(self._clock())
        return len(self._crashes) >= self.max_crashes

    def health_check(self, health: dict[str, Any]) -> bool:
        return self.heartbeat(health)

    def _prune(self, now: float) -> None:
        cutoff = now - self.crash_window_seconds
        while self._crashes and self._crashes[0] < cutoff:
            self._crashes.popleft()


__all__ = ["SupervisorClient", "SupervisorRegistration"]
