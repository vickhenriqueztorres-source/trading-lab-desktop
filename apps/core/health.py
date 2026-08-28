from __future__ import annotations

import threading
from dataclasses import dataclass

from packages.observability.events import EventSink, NullEventSink
from packages.persistence.health import (
    DatabaseFailureReason,
    DatabaseHealth,
    DatabaseHealthStatus,
)


@dataclass(frozen=True, slots=True)
class HealthState:
    is_open: bool
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class HealthGateSnapshot:
    global_state: HealthState
    scoped_states: dict[tuple[str, str], HealthState]
    active_blockers: tuple[str, ...]


class HealthGate:
    def __init__(
        self,
        database_health: DatabaseHealth | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._blockers: set[str] = set()
        self._scoped_blockers: dict[tuple[str, str], set[str]] = {}
        self._database_health = database_health
        self._event_sink = event_sink or NullEventSink()

    _PRIORITY = (
        "DB_NOT_CHECKED",
        "DB_SCHEMA_CORRUPT",
        "DB_JOURNAL_CORRUPT",
        "DB_FOREIGN_KEY_VIOLATION",
        "DB_CHECKPOINT_FAILED",
        "DB_WRITE_FAILED",
        "HG_SAFE_STOP",
        "HG_DAILY_STOP_REACHED",
        "HG_DAILY_TAKE_PROFIT_REACHED",
        "HG_COOLDOWN_ACTIVE",
        "HG_AUTH_AGENT_UNAVAILABLE",
        "HG_LEASE_EXPIRED",
        "HG_LEASE_REVOKED",
        "HG_ORDER_EVENT_CONFLICT",
        "HG_ORDER_EVENT_GAP",
        "HG_BROKER_EVENT_BACKPRESSURE",
        "HG_RECONCILIATION_CONFLICT",
        "HG_SETTLEMENT_REQUIRED",
        "HG_SETTLEMENT_UNKNOWN",
        "HG_ORDER_UNKNOWN",
        "HG_EXPOSURE_UNKNOWN",
        "HG_EXPOSURE_CURRENCY_MISMATCH",
        "HG_RECONCILIATION_UNAVAILABLE",
        "HG_RECONCILIATION_REQUIRED",
        "HG_WORKER_INCOMPATIBLE",
        "HG_WORKER_CIRCUIT_OPEN",
        "HG_WORKER_DISCONNECTED",
        "HG_WORKER_NOT_READY",
        "HG_MARKET_DATA_DISCONNECTED",
        "MD_CLOCK_UNTRUSTED",
    )

    @property
    def global_state(self) -> HealthState:
        database_state = self._database_state()
        if database_state is not None:
            return database_state
        with self._lock:
            return self._state_from_reasons(set(self._blockers))

    @property
    def state(self) -> HealthState:
        database_state = self._database_state()
        if database_state is not None:
            return database_state
        with self._lock:
            reasons = set(self._blockers)
            for scoped in self._scoped_blockers.values():
                reasons.update(scoped)
            return self._state_from_reasons(reasons)

    def state_for(self, broker: str, account_id: str) -> HealthState:
        database_state = self._database_state()
        if database_state is not None:
            return database_state
        with self._lock:
            global_reasons = set(self._blockers)
            if global_reasons:
                return self._state_from_reasons(global_reasons)
            normalized_broker = broker.upper()
            key = (normalized_broker, str(account_id))
            scoped_reasons = set(self._scoped_blockers.get(key, set()))
            # Broker-wide market-data readiness applies to every financial account.
            # Account-specific blockers remain isolated from other accounts.
            if str(account_id) != "market-data":
                scoped_reasons.update(
                    self._scoped_blockers.get((normalized_broker, "market-data"), set())
                )
            return self._state_from_reasons(scoped_reasons)

    def can_enter_order(self, broker: str, account_id: str) -> tuple[bool, str | None]:
        g_state = self.global_state
        if not g_state.is_open:
            return False, g_state.reason_code
        s_state = self.state_for(broker, account_id)
        if not s_state.is_open:
            return False, s_state.reason_code
        return True, None

    def register_broker_health(
        self,
        broker: str,
        account_id: str,
        is_ready: bool,
        reason_code: str | None = None,
    ) -> None:
        with self._lock:
            if is_ready:
                if reason_code is not None:
                    self.clear_scope(broker, account_id, reason_code)
                else:
                    self.clear_scope(broker, account_id, "HG_WORKER_DISCONNECTED")
                    self.clear_scope(broker, account_id, "HG_WORKER_NOT_READY")
            else:
                self.block_scope(broker, account_id, reason_code or "HG_WORKER_DISCONNECTED")

    def _database_state(self) -> HealthState | None:
        if self._database_health is not None:
            database_state = self._database_health.state
            if database_state.status is not DatabaseHealthStatus.HEALTHY:
                return HealthState(
                    is_open=False,
                    reason_code=(
                        database_state.reason.value
                        if database_state.reason is not None
                        else DatabaseFailureReason.DB_NOT_CHECKED.value
                    ),
                )
        return None

    @classmethod
    def _state_from_reasons(cls, reasons: set[str]) -> HealthState:
        if not reasons:
            return HealthState(is_open=True, reason_code=None)
        reason = next(
            (candidate for candidate in cls._PRIORITY if candidate in reasons),
            min(reasons),
        )
        return HealthState(is_open=False, reason_code=reason)

    def block(self, reason_code: str) -> None:
        with self._lock:
            changed = reason_code not in self._blockers
            self._blockers.add(reason_code)
        if changed:
            self._event_sink.emit("health_gate_blocked", reason_code=reason_code)

    def clear_if(self, reason_code: str) -> None:
        with self._lock:
            global_changed = reason_code in self._blockers
            self._blockers.discard(reason_code)
            cleared_scopes: list[tuple[str, str]] = []
            for key, scoped in self._scoped_blockers.items():
                if reason_code in scoped:
                    cleared_scopes.append(key)
                scoped.discard(reason_code)
        if global_changed:
            self._event_sink.emit("health_gate_cleared", reason_code=reason_code)
        for broker, account_id in cleared_scopes:
            self._event_sink.emit(
                "health_gate_cleared",
                reason_code=reason_code,
                broker=broker,
                account_id=account_id,
            )

    def clear(self, reason_code: str) -> None:
        self.clear_if(reason_code)

    def contains(self, reason_code: str) -> bool:
        with self._lock:
            if reason_code in self._blockers:
                return True
            return any(reason_code in reasons for reasons in self._scoped_blockers.values())

    def block_scope(self, broker: str, account_id: str, reason_code: str) -> None:
        key = (broker.upper(), str(account_id))
        with self._lock:
            blockers = self._scoped_blockers.setdefault(key, set())
            changed = reason_code not in blockers
            blockers.add(reason_code)
        if changed:
            self._event_sink.emit(
                "health_gate_blocked",
                reason_code=reason_code,
                broker=key[0],
                account_id=key[1],
            )

    def clear_scope(self, broker: str, account_id: str, reason_code: str) -> None:
        key = (broker.upper(), str(account_id))
        with self._lock:
            blockers = self._scoped_blockers.get(key)
            if blockers is None:
                return
            changed = reason_code in blockers
            blockers.discard(reason_code)
            if not blockers:
                self._scoped_blockers.pop(key, None)
        if changed:
            self._event_sink.emit(
                "health_gate_cleared",
                reason_code=reason_code,
                broker=key[0],
                account_id=key[1],
            )

    def fail_database(self, reason: DatabaseFailureReason) -> None:
        if self._database_health is not None:
            self._database_health.mark_failed(reason)
        self.block(reason.value)

    def ensure_open(self, broker: str | None = None, account_id: str | None = None) -> None:
        if (broker is None) != (account_id is None):
            raise ValueError("broker and account_id scope must be provided together")
        if broker is not None and account_id is not None:
            is_open, reason = self.can_enter_order(broker, account_id)
            if not is_open:
                raise RuntimeError(f"Health Gate blocked: {reason}")
        else:
            state = self.global_state
            if not state.is_open:
                raise RuntimeError(f"Health Gate blocked: {state.reason_code}")

    def get_snapshot(self) -> HealthGateSnapshot:
        with self._lock:
            g_state = self.global_state
            scoped: dict[tuple[str, str], HealthState] = {}
            for key in self._scoped_blockers:
                scoped[key] = self.state_for(key[0], key[1])
            all_blockers = set(self._blockers)
            for s_blockers in self._scoped_blockers.values():
                all_blockers.update(s_blockers)
            return HealthGateSnapshot(
                global_state=g_state,
                scoped_states=scoped,
                active_blockers=tuple(sorted(all_blockers)),
            )


CoreHealthGate = HealthGate
