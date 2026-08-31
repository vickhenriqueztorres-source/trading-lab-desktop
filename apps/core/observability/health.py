"""Liveness/readiness/trading-readiness projection for a worker."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class HealthStatus:
    liveness: bool
    readiness: bool
    trading_readiness: bool
    state: str
    connection_state: str
    auth_state: str
    breakers_state: Mapping[str, str] = field(default_factory=dict)
    last_reconciliation: str | None = None
    pending_unknown: int = 0
    uptime: float = 0.0
    lease_state: str = "NONE"
    fencing_token: int | None = None
    is_leader: bool = False


class HealthChecker:
    def __init__(
        self,
        *,
        state: Callable[[], str],
        connected: Callable[[], bool],
        authenticated: Callable[[], bool],
        breakers: Mapping[str, Callable[[], str]] | None = None,
        last_reconciliation: Callable[[], str | None] | None = None,
        pending_unknown: Callable[[], int] | None = None,
        started_at: float | None = None,
        lease_state: Callable[[], str] | None = None,
        fencing_token: Callable[[], int | None] | None = None,
        is_leader: Callable[[], bool] | None = None,
    ) -> None:
        self._state = state
        self._connected = connected
        self._authenticated = authenticated
        self._breakers = dict(breakers or {})
        self._last_reconciliation = last_reconciliation or (lambda: None)
        self._pending_unknown = pending_unknown or (lambda: 0)
        self._started_at = started_at if started_at is not None else time.monotonic()
        self._lease_state = lease_state or (lambda: "NONE")
        self._fencing_token = fencing_token or (lambda: None)
        self._is_leader = is_leader or (lambda: False)

    def get_status(self) -> HealthStatus:
        worker_state = self._state()
        connection_state = "CONNECTED" if self._connected() else "DISCONNECTED"
        auth_state = "AUTHENTICATED" if self._authenticated() else "UNAUTHENTICATED"
        breaker_states = {name: getter() for name, getter in self._breakers.items()}
        blockers = any(value == "OPEN" for value in breaker_states.values())
        ready = worker_state in {"READY", "READ_ONLY"} and not blockers
        return HealthStatus(
            liveness=worker_state not in {"HALTED", "SHUTTING_DOWN"},
            readiness=ready,
            trading_readiness=worker_state == "READY" and not blockers,
            state=worker_state,
            connection_state=connection_state,
            auth_state=auth_state,
            breakers_state=breaker_states,
            last_reconciliation=self._last_reconciliation(),
            pending_unknown=self._pending_unknown(),
            uptime=max(0.0, time.monotonic() - self._started_at),
            lease_state=self._lease_state(),
            fencing_token=self._fencing_token(),
            is_leader=self._is_leader(),
        )

    def get_metrics(self) -> dict[str, object]:
        status = self.get_status()
        return {
            "uptime": status.uptime,
            "pending_unknown": status.pending_unknown,
            "liveness": status.liveness,
            "readiness": status.readiness,
            "trading_readiness": status.trading_readiness,
        }


__all__ = ["HealthChecker", "HealthStatus"]
