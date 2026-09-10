"""Execution intent and transport availability are independent state machines."""

from __future__ import annotations

import json
import os
import threading
from enum import StrEnum
from pathlib import Path


class ExecutionState(StrEnum):
    DISARMED = "DISARMED"
    ARMED = "ARMED"
    ARMED_DEGRADED = "ARMED_DEGRADED"
    STOPPED_BY_RISK = "STOPPED_BY_RISK"
    STOPPED_BY_STRATEGY = "STOPPED_BY_STRATEGY"
    STOPPED_BY_PAYOUT = "STOPPED_BY_PAYOUT"


class StopReason(StrEnum):
    RISK_MANAGER = "RISK_MANAGER"
    STRATEGY_GATE = "STRATEGY_GATE"
    PAYOUT_GATE = "PAYOUT_GATE"
    USER_COMMAND = "USER_COMMAND"


ALLOWED_STOP_REASONS = frozenset(StopReason)

_STOP_STATES = {
    StopReason.RISK_MANAGER: ExecutionState.STOPPED_BY_RISK,
    StopReason.STRATEGY_GATE: ExecutionState.STOPPED_BY_STRATEGY,
    StopReason.PAYOUT_GATE: ExecutionState.STOPPED_BY_PAYOUT,
    StopReason.USER_COMMAND: ExecutionState.DISARMED,
}


class OperatorIntentStore:
    """Atomic, non-secret persistence for the operator's IQ Option arm intent."""

    _SCHEMA_VERSION = 1

    def __init__(self, profile_dir: Path) -> None:
        self.path = Path(profile_dir) / "operator_intent.json"

    def load_armed(self) -> bool:
        if not self.path.exists():
            return False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("IQOPTION_OPERATOR_INTENT_INVALID") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != self._SCHEMA_VERSION
            or type(payload.get("armed")) is not bool
            or set(payload) != {"schema_version", "armed"}
        ):
            raise ValueError("IQOPTION_OPERATOR_INTENT_INVALID")
        return bool(payload["armed"])

    def save_armed(self, armed: bool) -> None:
        if type(armed) is not bool:
            raise TypeError("armed intent must be a boolean")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {"schema_version": self._SCHEMA_VERSION, "armed": armed},
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


class TransportSupervisor:
    """Keep operator intent armed while transport is temporarily unavailable."""

    def __init__(
        self,
        *,
        intent_store: OperatorIntentStore | None = None,
        initially_armed: bool | None = None,
    ) -> None:
        if initially_armed is not None and type(initially_armed) is not bool:
            raise TypeError("initially_armed must be a boolean")
        self._intent_store = intent_store
        armed = intent_store.load_armed() if initially_armed is None and intent_store else False
        if initially_armed is not None:
            armed = initially_armed
        self._state = ExecutionState.ARMED_DEGRADED if armed else ExecutionState.DISARMED
        self._last_transport_reason: str | None = "TRANSPORT_DOWN" if armed else None
        self._lock = threading.RLock()

    @property
    def state(self) -> ExecutionState:
        with self._lock:
            return self._state

    @property
    def armed_intent(self) -> bool:
        with self._lock:
            return self._state in {ExecutionState.ARMED, ExecutionState.ARMED_DEGRADED}

    @property
    def last_transport_reason(self) -> str | None:
        with self._lock:
            return self._last_transport_reason

    def arm(self) -> ExecutionState:
        with self._lock:
            self._persist(True)
            self._state = ExecutionState.ARMED
            self._last_transport_reason = None
            return self._state

    def safe_stop(
        self,
        *,
        caller: StopReason,
        persist_intent: bool = True,
    ) -> ExecutionState:
        assert caller in ALLOWED_STOP_REASONS, "unauthorized execution stop caller"
        with self._lock:
            if persist_intent:
                self._persist(False)
            self._state = _STOP_STATES[caller]
            self._last_transport_reason = None
            return self._state

    def mark_down(self, reason_code: str) -> ExecutionState:
        if not reason_code:
            raise ValueError("transport reason is required")
        with self._lock:
            if self._state in {ExecutionState.ARMED, ExecutionState.ARMED_DEGRADED}:
                self._state = ExecutionState.ARMED_DEGRADED
            self._last_transport_reason = reason_code
            return self._state

    def mark_up(self) -> ExecutionState:
        with self._lock:
            if self._state is ExecutionState.ARMED_DEGRADED:
                self._state = ExecutionState.ARMED
            self._last_transport_reason = None
            return self._state

    def _persist(self, armed: bool) -> None:
        if self._intent_store is not None:
            self._intent_store.save_armed(armed)


__all__ = [
    "ALLOWED_STOP_REASONS",
    "ExecutionState",
    "OperatorIntentStore",
    "StopReason",
    "TransportSupervisor",
]
