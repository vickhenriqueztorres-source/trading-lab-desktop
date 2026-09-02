"""Prevent accidental IQ Option login storms and message bursts.

The IQ Option connector is community-maintained and has no published request
quota that this application can rely on.  These limits are therefore internal,
conservative safety ceilings.  They are not an attempt to discover or evade a
broker limit.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

IQOPTION_HTTP_LOGIN_WINDOW_SECONDS = 15 * 60
IQOPTION_HTTP_LOGIN_LIMIT = 3
IQOPTION_CONNECTION_QUARANTINE_SECONDS = 15 * 60
IQOPTION_MAX_AUTOMATED_RECOVERY_ATTEMPTS = 5

# One request per evaluation second is the maximum market-data load.  A
# separate 30-message operational reserve is deliberately left for balance,
# order events and reconciliation.  This is an application limit, not a
# representation of an official IQ Option quota.
IQOPTION_TOTAL_INTERNAL_MESSAGE_BUDGET_PER_MINUTE = 90
IQOPTION_MARKET_DATA_MESSAGE_BUDGET_PER_MINUTE = 60

_IMMEDIATE_QUARANTINE_REASONS = frozenset(
    {
        "IQOPTION_AUTH_FAILED",
        "IQOPTION_2FA_REQUIRED",
        "IQOPTION_RATE_LIMITED",
    }
)


class IQOptionConnectionSafetyStateError(RuntimeError):
    """Raised when the persistent protection state cannot be trusted."""


@dataclass(frozen=True, slots=True)
class IQOptionConnectionSafetyDecision:
    allowed: bool
    reason_code: str
    attempts_in_window: int
    retry_after_seconds: int


@dataclass(frozen=True, slots=True)
class IQOptionConnectionSafetySnapshot:
    attempts_in_window: int
    quarantine_active: bool
    retry_after_seconds: int
    last_reason_code: str | None


@dataclass(frozen=True, slots=True)
class IQOptionMessageBudgetDecision:
    allowed: bool
    used_in_window: int
    limit: int
    pressure: bool


@dataclass(frozen=True, slots=True)
class _PersistentState:
    attempt_epochs: tuple[float, ...] = ()
    quarantine_until_epoch: float = 0.0
    last_reason_code: str | None = None


class IQOptionConnectionSafetyStore:
    """Atomic, non-secret persistent state owned by the Core profile."""

    _SCHEMA_VERSION = 1

    def __init__(self, profile_dir: Path) -> None:
        self._path = Path(profile_dir) / "iqoption-connection-safety.json"

    def load(self) -> _PersistentState:
        if not self._path.exists():
            return _PersistentState()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != self._SCHEMA_VERSION:
                raise ValueError
            raw_attempts = raw.get("attempt_epochs")
            raw_quarantine = raw.get("quarantine_until_epoch")
            raw_reason = raw.get("last_reason_code")
            if not isinstance(raw_attempts, list):
                raise ValueError
            if isinstance(raw_quarantine, bool) or not isinstance(raw_quarantine, (int, float)):
                raise ValueError
            attempts: list[float] = []
            for item in raw_attempts:
                if isinstance(item, bool) or not isinstance(item, (int, float)):
                    raise ValueError
                value = float(item)
                if not math.isfinite(value) or value < 0:
                    raise ValueError
                attempts.append(value)
            quarantine = float(raw_quarantine)
            if not math.isfinite(quarantine) or quarantine < 0:
                raise ValueError
            if raw_reason is not None and not isinstance(raw_reason, str):
                raise ValueError
            return _PersistentState(tuple(attempts), quarantine, raw_reason)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise IQOptionConnectionSafetyStateError(
                "IQOPTION_CONNECTION_SAFETY_STATE_INVALID"
            ) from exc

    def save(self, state: _PersistentState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        payload = {
            "schema_version": self._SCHEMA_VERSION,
            "attempt_epochs": list(state.attempt_epochs),
            "quarantine_until_epoch": state.quarantine_until_epoch,
            "last_reason_code": state.last_reason_code,
        }
        try:
            temporary.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, self._path)
        except OSError as exc:
            raise IQOptionConnectionSafetyStateError(
                "IQOPTION_CONNECTION_SAFETY_STATE_INVALID"
            ) from exc


class IQOptionConnectionSafetyController:
    """Persistent admission control for external IQ Option session starts."""

    def __init__(
        self,
        store: IQOptionConnectionSafetyStore,
        *,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._wall_time = wall_time
        self._lock = threading.Lock()
        self._state = store.load()

    def admit_http_login(self) -> IQOptionConnectionSafetyDecision:
        """Reserve one external session-start attempt before network activity."""

        with self._lock:
            now = self._now()
            state = self._pruned(now)
            if state.quarantine_until_epoch > now:
                self._state = state
                return self._decision(False, state, now)
            if len(state.attempt_epochs) >= IQOPTION_HTTP_LOGIN_LIMIT:
                state = _PersistentState(
                    state.attempt_epochs,
                    now + IQOPTION_CONNECTION_QUARANTINE_SECONDS,
                    "IQOPTION_HTTP_LOGIN_LIMIT_REACHED",
                )
                self._persist(state)
                return self._decision(False, state, now)
            state = _PersistentState(
                (*state.attempt_epochs, now),
                0.0,
                state.last_reason_code,
            )
            self._persist(state)
            return self._decision(True, state, now)

    def record_failure(self, reason_code: str) -> None:
        with self._lock:
            now = self._now()
            state = self._pruned(now)
            quarantine_until = state.quarantine_until_epoch
            if reason_code in _IMMEDIATE_QUARANTINE_REASONS:
                quarantine_until = max(
                    quarantine_until,
                    now + IQOPTION_CONNECTION_QUARANTINE_SECONDS,
                )
            self._persist(_PersistentState(state.attempt_epochs, quarantine_until, reason_code))

    def record_success(self) -> None:
        """Clear failure status, but retain the rolling anti-storm history."""

        with self._lock:
            now = self._now()
            state = self._pruned(now)
            self._persist(_PersistentState(state.attempt_epochs, 0.0, None))

    def snapshot(self) -> IQOptionConnectionSafetySnapshot:
        with self._lock:
            now = self._now()
            state = self._pruned(now)
            self._state = state
            retry_after = max(0, math.ceil(state.quarantine_until_epoch - now))
            return IQOptionConnectionSafetySnapshot(
                attempts_in_window=len(state.attempt_epochs),
                quarantine_active=retry_after > 0,
                retry_after_seconds=retry_after,
                last_reason_code=state.last_reason_code,
            )

    def _pruned(self, now: float) -> _PersistentState:
        boundary = now - IQOPTION_HTTP_LOGIN_WINDOW_SECONDS
        return _PersistentState(
            tuple(item for item in self._state.attempt_epochs if item > boundary),
            self._state.quarantine_until_epoch,
            self._state.last_reason_code,
        )

    def _persist(self, state: _PersistentState) -> None:
        self._store.save(state)
        self._state = state

    @staticmethod
    def _decision(
        allowed: bool,
        state: _PersistentState,
        now: float,
    ) -> IQOptionConnectionSafetyDecision:
        retry_after = max(0, math.ceil(state.quarantine_until_epoch - now))
        reason = (
            "IQOPTION_CONNECTION_ATTEMPT_ADMITTED" if allowed else "IQOPTION_CONNECTION_QUARANTINED"
        )
        return IQOptionConnectionSafetyDecision(
            allowed=allowed,
            reason_code=reason,
            attempts_in_window=len(state.attempt_epochs),
            retry_after_seconds=retry_after,
        )

    def _now(self) -> float:
        now = float(self._wall_time())
        if not math.isfinite(now) or now < 0:
            raise IQOptionConnectionSafetyStateError("IQOPTION_CONNECTION_SAFETY_STATE_INVALID")
        return now


class IQOptionMessageBudget:
    """Thread-safe sliding-window budget for nonfinancial market-data reads."""

    def __init__(
        self,
        *,
        limit: int = IQOPTION_MARKET_DATA_MESSAGE_BUDGET_PER_MINUTE,
        pressure_at: int | None = None,
    ) -> None:
        if limit <= 0:
            raise ValueError("IQ Option message budget must be positive")
        resolved_pressure = pressure_at if pressure_at is not None else max(1, (limit * 4 + 4) // 5)
        if not 1 <= resolved_pressure <= limit:
            raise ValueError("IQ Option message pressure threshold is invalid")
        self._limit = limit
        self._pressure_at = resolved_pressure
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def try_acquire(self, now_monotonic: float) -> IQOptionMessageBudgetDecision:
        if not math.isfinite(now_monotonic) or now_monotonic < 0:
            raise ValueError("IQ Option message budget time is invalid")
        with self._lock:
            boundary = now_monotonic - 60.0
            while self._timestamps and self._timestamps[0] <= boundary:
                self._timestamps.popleft()
            used = len(self._timestamps)
            allowed = used < self._limit
            if allowed:
                self._timestamps.append(now_monotonic)
                used += 1
            return IQOptionMessageBudgetDecision(
                allowed=allowed,
                used_in_window=used,
                limit=self._limit,
                pressure=used >= self._pressure_at,
            )


__all__ = [
    "IQOPTION_CONNECTION_QUARANTINE_SECONDS",
    "IQOPTION_HTTP_LOGIN_LIMIT",
    "IQOPTION_HTTP_LOGIN_WINDOW_SECONDS",
    "IQOPTION_MARKET_DATA_MESSAGE_BUDGET_PER_MINUTE",
    "IQOPTION_MAX_AUTOMATED_RECOVERY_ATTEMPTS",
    "IQOPTION_TOTAL_INTERNAL_MESSAGE_BUDGET_PER_MINUTE",
    "IQOptionConnectionSafetyController",
    "IQOptionConnectionSafetyDecision",
    "IQOptionConnectionSafetySnapshot",
    "IQOptionConnectionSafetyStateError",
    "IQOptionConnectionSafetyStore",
    "IQOptionMessageBudget",
    "IQOptionMessageBudgetDecision",
]
