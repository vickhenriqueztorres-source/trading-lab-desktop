from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from packages.domain.models import (
    BrokerEvent,
    BrokerOrderEvent,
    ExternalOrderStatus,
    OrderCommand,
    OrderRequest,
    OrderState,
    OutboxState,
    ReconciliationEvidence,
    RiskReservationState,
    TradeIntentState,
    utc_now,
)
from packages.observability.events import EventSink, NullEventSink
from packages.persistence.database import (
    DatabaseIntegrityError,
    DatabaseMissingError,
    IntegrityCheckMode,
    configure_writer_connection,
    connect_database,
    ensure_database_presence_is_safe,
    mark_database_expected,
    verify_database_integrity,
)
from packages.persistence.health import DatabaseFailureReason, DatabaseHealth
from packages.persistence.migrations import MigrationError, apply_migrations


class PersistenceError(RuntimeError):
    pass


class RiskLimitExceededError(PersistenceError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class DatabaseStartupError(PersistenceError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class DatabaseWriteError(PersistenceError):
    reason_code = DatabaseFailureReason.DB_WRITE_FAILED.value


class AccountBusyError(PersistenceError):
    pass


class InvalidOrderTransition(PersistenceError):
    pass


class ReservationReleaseBlocked(PersistenceError):
    pass


class ReconciliationApplyStatus(StrEnum):
    RESOLVED = "RESOLVED"
    IDEMPOTENT = "IDEMPOTENT"
    UNRESOLVED = "UNRESOLVED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class ReconciliationApplyResult:
    status: ReconciliationApplyStatus
    order_state: OrderState
    reason_code: str | None


class BrokerEventApplyStatus(StrEnum):
    APPLIED = "APPLIED"
    APPLIED_WITH_GAP = "APPLIED_WITH_GAP"
    DUPLICATE = "DUPLICATE"
    LATE_IGNORED = "LATE_IGNORED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class BrokerEventApplyResult:
    status: BrokerEventApplyStatus
    order_state: OrderState | None
    reason_code: str | None


FaultInjector = Callable[[str], None]


ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.OUTBOXED: frozenset({OrderState.DISPATCHING, OrderState.REJECTED}),
    OrderState.DISPATCHING: frozenset(
        {OrderState.ACCEPTED, OrderState.REJECTED, OrderState.UNKNOWN, OrderState.SEND_BLOCKED}
    ),
    OrderState.ACCEPTED: frozenset(
        {OrderState.OPEN, OrderState.SETTLED, OrderState.SETTLEMENT_UNKNOWN}
    ),
    OrderState.OPEN: frozenset({OrderState.SETTLED, OrderState.SETTLEMENT_UNKNOWN}),
    OrderState.UNKNOWN: frozenset({OrderState.RECONCILING}),
    OrderState.RECONCILING: frozenset(
        {
            OrderState.ACCEPTED,
            OrderState.OPEN,
            OrderState.SETTLED,
            OrderState.REJECTED,
            OrderState.MANUAL_REVIEW,
            OrderState.SETTLEMENT_UNKNOWN,
        }
    ),
    OrderState.SETTLEMENT_UNKNOWN: frozenset({OrderState.RECONCILING, OrderState.MANUAL_REVIEW}),
    OrderState.MANUAL_REVIEW: frozenset({OrderState.RECONCILING}),
    OrderState.SETTLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.SEND_BLOCKED: frozenset(),
}


class SingleDatabaseWriter:
    """The only write-capable boundary for Core financial state."""

    def __init__(
        self,
        path: Path,
        fault_injector: FaultInjector | None = None,
        *,
        database_health: DatabaseHealth | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._fault_injector = fault_injector
        self.database_health = database_health or DatabaseHealth()
        self._event_sink = event_sink or NullEventSink()
        connection: sqlite3.Connection | None = None
        try:
            first_run = ensure_database_presence_is_safe(path)
            connection = connect_database(path)
            self._event_sink.emit("database_opened", first_run=first_run)
            verify_database_integrity(connection, IntegrityCheckMode.QUICK)
            configure_writer_connection(connection)
            apply_migrations(connection)
            verify_database_integrity(connection, IntegrityCheckMode.QUICK)
            mark_database_expected(path)
            self._connection = connection
            self.database_health.mark_healthy()
            self._event_sink.emit(
                "database_integrity_checked",
                check_mode=IntegrityCheckMode.QUICK.value,
            )
        except DatabaseMissingError as exc:
            self._fail_startup(DatabaseFailureReason.DB_MISSING_UNEXPECTED, connection)
            raise DatabaseStartupError(
                DatabaseFailureReason.DB_MISSING_UNEXPECTED.value,
                "expected critical database is missing",
            ) from exc
        except DatabaseIntegrityError as exc:
            self._fail_startup(DatabaseFailureReason.DB_INTEGRITY_FAILED, connection)
            raise DatabaseStartupError(
                DatabaseFailureReason.DB_INTEGRITY_FAILED.value,
                "critical database integrity verification failed",
            ) from exc
        except MigrationError as exc:
            self._fail_startup(DatabaseFailureReason.DB_MIGRATION_FAILED, connection)
            raise DatabaseStartupError(
                exc.reason_code,
                "critical database migration failed",
            ) from exc
        except (sqlite3.Error, OSError) as exc:
            self._fail_startup(DatabaseFailureReason.DB_OPEN_FAILED, connection)
            raise DatabaseStartupError(
                DatabaseFailureReason.DB_OPEN_FAILED.value,
                "critical database initialization failed",
            ) from exc

    def _fail_startup(
        self,
        reason: DatabaseFailureReason,
        connection: sqlite3.Connection | None,
    ) -> None:
        self.database_health.mark_failed(reason)
        self._event_sink.emit("database_failure", reason_code=reason.value)
        if connection is not None:
            connection.close()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def run_integrity_check(
        self,
        mode: IntegrityCheckMode = IntegrityCheckMode.FULL,
    ) -> None:
        with self._lock:
            try:
                verify_database_integrity(self._connection, mode)
            except DatabaseIntegrityError:
                self.database_health.mark_failed(DatabaseFailureReason.DB_INTEGRITY_FAILED)
                self._event_sink.emit(
                    "database_failure",
                    reason_code=DatabaseFailureReason.DB_INTEGRITY_FAILED.value,
                )
                raise
            self._event_sink.emit("database_integrity_checked", check_mode=mode.value)

    def backup_to_connection(self, destination: sqlite3.Connection) -> None:
        with self._lock:
            try:
                self._connection.backup(destination)
            except sqlite3.Error as exc:
                self._mark_write_failed()
                raise DatabaseWriteError("SQLite backup failed") from exc

    def _inject(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)

    def _transaction(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                result = operation(self._connection)
                self._connection.execute("COMMIT")
                return result
            except sqlite3.IntegrityError as exc:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                if "risk_reservations.broker, risk_reservations.account_id" in str(exc):
                    raise AccountBusyError("account already has active exposure") from exc
                raise PersistenceError("database integrity constraint rejected the write") from exc
            except PersistenceError:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
            except Exception as exc:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                self._mark_write_failed()
                raise DatabaseWriteError("critical database transaction failed") from exc

    def _mark_write_failed(self) -> None:
        self.database_health.mark_failed(DatabaseFailureReason.DB_WRITE_FAILED)
        self._event_sink.emit(
            "database_failure",
            reason_code=DatabaseFailureReason.DB_WRITE_FAILED.value,
        )

    def persist_intent_reservation_outbox(
        self,
        *,
        request: OrderRequest,
        command: OrderCommand,
        intent_id: str,
        reservation_id: str,
        order_id: str,
        created_at: datetime,
        global_max_exposure_minor_units: int | None = None,
        max_exposure_per_symbol_minor_units: int | None = None,
    ) -> None:
        payload = json.dumps(command.to_payload(), sort_keys=True, separators=(",", ":"))

        def operation(connection: sqlite3.Connection) -> None:
            if global_max_exposure_minor_units is not None:
                row = connection.execute(
                    "SELECT COALESCE(SUM(amount_minor), 0) AS total "
                    "FROM risk_reservations WHERE state = ?",
                    (RiskReservationState.ACTIVE.value,),
                ).fetchone()
                active_global = int(row["total"])
                if active_global + request.amount.minor_units > global_max_exposure_minor_units:
                    raise RiskLimitExceededError(
                        "HG_GLOBAL_EXPOSURE_EXCEEDED",
                        f"Active global exposure ({active_global + request.amount.minor_units}) "
                        f"exceeds limit ({global_max_exposure_minor_units})",
                    )
            if max_exposure_per_symbol_minor_units is not None:
                clean_sym = request.symbol.strip().upper()
                sym_variants = (
                    clean_sym,
                    f"FRX{clean_sym}" if not clean_sym.startswith("FRX") else clean_sym[3:],
                )
                placeholders = ",".join("?" for _ in sym_variants)
                row = connection.execute(
                    f"""
                    SELECT COALESCE(SUM(r.amount_minor), 0) AS total
                    FROM risk_reservations r
                    JOIN trade_intents t ON t.intent_id = r.intent_id
                    WHERE r.state = ? AND UPPER(t.symbol) IN ({placeholders})
                    """,
                    (RiskReservationState.ACTIVE.value, *sym_variants),
                ).fetchone()
                active_symbol = int(row["total"])
                if active_symbol + request.amount.minor_units > max_exposure_per_symbol_minor_units:
                    raise RiskLimitExceededError(
                        "HG_SYMBOL_EXPOSURE_LIMIT_EXCEEDED",
                        f"Active symbol exposure on {request.symbol} "
                        f"({active_symbol + request.amount.minor_units}) exceeds limit "
                        f"({max_exposure_per_symbol_minor_units})",
                    )
            connection.execute(
                """
                INSERT INTO trade_intents(
                    intent_id, correlation_id, broker, account_id, product, symbol,
                    direction, amount_minor, currency, status, created_at,
                    strategy_id, strategy_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent_id,
                    request.correlation_id,
                    request.broker.value,
                    request.account_id,
                    request.product,
                    request.symbol,
                    request.direction.value,
                    request.amount.minor_units,
                    request.amount.currency,
                    TradeIntentState.CREATED.value,
                    created_at.isoformat(),
                    request.strategy_id,
                    request.strategy_version,
                ),
            )
            self._inject("after_intent")
            connection.execute(
                """
                INSERT INTO risk_reservations(
                    reservation_id, intent_id, broker, account_id, amount_minor,
                    currency, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reservation_id,
                    intent_id,
                    request.broker.value,
                    request.account_id,
                    request.amount.minor_units,
                    request.amount.currency,
                    RiskReservationState.ACTIVE.value,
                    created_at.isoformat(),
                ),
            )
            self._inject("after_reservation")
            connection.execute(
                """
                INSERT INTO outbox_messages(
                    message_id, correlation_id, intent_id, message_type, payload,
                    state, created_at, available_at, attempt_count
                ) VALUES (?, ?, ?, 'OrderCommand', ?, ?, ?, ?, 0)
                """,
                (
                    command.message_id,
                    request.correlation_id,
                    intent_id,
                    payload,
                    OutboxState.PENDING.value,
                    created_at.isoformat(),
                    created_at.isoformat(),
                ),
            )
            self._inject("after_outbox")
            connection.execute(
                """
                INSERT INTO orders(
                    order_id, intent_id, broker, account_id, state, correlation_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    intent_id,
                    request.broker.value,
                    request.account_id,
                    OrderState.OUTBOXED.value,
                    request.correlation_id,
                    created_at.isoformat(),
                    created_at.isoformat(),
                ),
            )
            self._inject("before_commit")

        self._transaction(operation)

    def claim_next_message(
        self,
        now: datetime | None = None,
        *,
        broker: str | None = None,
        account_id: str | None = None,
    ) -> OrderCommand | None:
        claimed_at = now or utc_now()

        def operation(connection: sqlite3.Connection) -> OrderCommand | None:
            if broker is not None and account_id is not None:
                row = connection.execute(
                    """
                    SELECT om.message_id, om.intent_id, om.payload, o.order_id
                    FROM outbox_messages om
                    JOIN orders o ON o.intent_id = om.intent_id
                    WHERE om.state = ? AND om.available_at <= ?
                      AND o.broker = ? AND o.account_id = ?
                    ORDER BY om.created_at, om.message_id
                    LIMIT 1
                    """,
                    (OutboxState.PENDING.value, claimed_at.isoformat(), broker, account_id),
                ).fetchone()
            elif broker is not None:
                row = connection.execute(
                    """
                    SELECT om.message_id, om.intent_id, om.payload, o.order_id
                    FROM outbox_messages om
                    JOIN orders o ON o.intent_id = om.intent_id
                    WHERE om.state = ? AND om.available_at <= ? AND o.broker = ?
                    ORDER BY om.created_at, om.message_id
                    LIMIT 1
                    """,
                    (OutboxState.PENDING.value, claimed_at.isoformat(), broker),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT om.message_id, om.intent_id, om.payload, o.order_id
                    FROM outbox_messages om
                    JOIN orders o ON o.intent_id = om.intent_id
                    WHERE om.state = ? AND om.available_at <= ?
                    ORDER BY om.created_at, om.message_id
                    LIMIT 1
                    """,
                    (OutboxState.PENDING.value, claimed_at.isoformat()),
                ).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                """
                UPDATE outbox_messages
                SET state = ?, dispatch_started_at = ?, attempt_count = attempt_count + 1
                WHERE message_id = ? AND state = ?
                """,
                (
                    OutboxState.DISPATCHING.value,
                    claimed_at.isoformat(),
                    row["message_id"],
                    OutboxState.PENDING.value,
                ),
            ).rowcount
            if changed != 1:
                return None
            self._transition_order(connection, row["intent_id"], OrderState.DISPATCHING, claimed_at)
            payload = json.loads(row["payload"])
            if not isinstance(payload, dict):
                raise PersistenceError("outbox payload is not an object")
            payload.setdefault("order_id", row["order_id"])
            return OrderCommand.from_payload(payload)

        result = self._transaction(operation)
        if result is None or isinstance(result, OrderCommand):
            return result
        raise PersistenceError("unexpected outbox claim result")

    def record_dispatch_result(
        self,
        command: OrderCommand,
        outcome: str,
        *,
        broker_order_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        recorded_at = now or utc_now()

        def operation(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                "SELECT state FROM outbox_messages WHERE message_id = ?",
                (command.message_id,),
            ).fetchone()
            if row is None or row["state"] != OutboxState.DISPATCHING.value:
                raise PersistenceError("outbox message is not in DISPATCHING")
            if outcome == "ACCEPTED":
                outbox_state = OutboxState.DISPATCHED
                order_state = OrderState.ACCEPTED
            elif outcome == "REJECTED":
                outbox_state = OutboxState.DISPATCHED
                order_state = OrderState.REJECTED
            elif outcome == "TIMEOUT_AFTER_POSSIBLE_SEND":
                outbox_state = OutboxState.AMBIGUOUS
                order_state = OrderState.UNKNOWN
            else:
                raise PersistenceError("unsupported worker outcome")
            connection.execute(
                """
                UPDATE outbox_messages SET state = ?, dispatched_at = ?, state_reason = ?
                WHERE message_id = ?
                """,
                (
                    outbox_state.value,
                    (recorded_at.isoformat() if outbox_state is OutboxState.DISPATCHED else None),
                    ("POSSIBLE_SEND_TIMEOUT" if outbox_state is OutboxState.AMBIGUOUS else None),
                    command.message_id,
                ),
            )
            self._transition_order(
                connection,
                command.intent_id,
                order_state,
                recorded_at,
                broker_order_id=broker_order_id,
            )
            if order_state is OrderState.REJECTED:
                self._release_for_intent(
                    connection,
                    command.intent_id,
                    recorded_at,
                    reason="BROKER_REJECTED",
                    evidence=None,
                )

        self._transaction(operation)

    def record_dispatch_not_sent(
        self,
        command: OrderCommand,
        *,
        reason_code: str,
        now: datetime | None = None,
    ) -> None:
        recorded_at = now or utc_now()

        def operation(connection: sqlite3.Connection) -> None:
            changed = connection.execute(
                """
                UPDATE outbox_messages
                SET state = ?, state_reason = ?
                WHERE message_id = ? AND state = ?
                """,
                (
                    OutboxState.BLOCKED_NOT_SENT.value,
                    reason_code,
                    command.message_id,
                    OutboxState.DISPATCHING.value,
                ),
            ).rowcount
            if changed != 1:
                raise PersistenceError("outbox message is not in DISPATCHING")
            self._transition_order(
                connection,
                command.intent_id,
                OrderState.SEND_BLOCKED,
                recorded_at,
            )

        self._transaction(operation)

    def cancel_expired_before_dispatch(
        self, command: OrderCommand, now: datetime | None = None
    ) -> None:
        cancelled_at = now or utc_now()

        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                UPDATE outbox_messages
                SET state = ?, state_reason = ?
                WHERE message_id = ? AND state = ?
                """,
                (
                    OutboxState.CANCELLED.value,
                    "CANCELLED_EXPIRED",
                    command.message_id,
                    OutboxState.DISPATCHING.value,
                ),
            )
            self._transition_order(connection, command.intent_id, OrderState.REJECTED, cancelled_at)
            self._release_for_intent(
                connection,
                command.intent_id,
                cancelled_at,
                reason="DEADLINE_EXPIRED_BEFORE_DISPATCH",
                evidence=None,
            )

        self._transaction(operation)

    def recover_interrupted_dispatches(self, now: datetime | None = None) -> int:
        recovered_at = now or utc_now()

        def operation(connection: sqlite3.Connection) -> int:
            rows = connection.execute(
                "SELECT intent_id FROM outbox_messages WHERE state = ?",
                (OutboxState.DISPATCHING.value,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE outbox_messages SET state = ?, state_reason = ?
                    WHERE intent_id = ? AND state = ?
                    """,
                    (
                        OutboxState.AMBIGUOUS.value,
                        "INTERRUPTED_DISPATCH",
                        row["intent_id"],
                        OutboxState.DISPATCHING.value,
                    ),
                )
                self._transition_order(
                    connection, row["intent_id"], OrderState.UNKNOWN, recovered_at
                )
            return len(rows)

        return int(self._transaction(operation))

    def cancel_expired_pending_messages(self, now: datetime | None = None) -> tuple[str, ...]:
        checked_at = now or utc_now()

        def operation(connection: sqlite3.Connection) -> tuple[str, ...]:
            rows = connection.execute(
                """
                SELECT om.message_id, om.intent_id, om.payload, o.order_id
                FROM outbox_messages om
                JOIN orders o ON o.intent_id = om.intent_id
                WHERE om.state = ?
                ORDER BY om.created_at, om.message_id
                """,
                (OutboxState.PENDING.value,),
            ).fetchall()
            cancelled: list[str] = []
            for row in rows:
                payload = json.loads(row["payload"])
                if not isinstance(payload, dict):
                    raise PersistenceError("outbox payload is not an object")
                payload.setdefault("order_id", row["order_id"])
                command = OrderCommand.from_payload(payload)
                if command.deadline_at > checked_at:
                    continue
                connection.execute(
                    """
                    UPDATE outbox_messages
                    SET state = ?, state_reason = ?
                    WHERE message_id = ? AND state = ?
                    """,
                    (
                        OutboxState.CANCELLED.value,
                        "CANCELLED_EXPIRED",
                        row["message_id"],
                        OutboxState.PENDING.value,
                    ),
                )
                self._transition_order(
                    connection,
                    row["intent_id"],
                    OrderState.REJECTED,
                    checked_at,
                )
                self._release_for_intent(
                    connection,
                    row["intent_id"],
                    checked_at,
                    reason="DEADLINE_EXPIRED_ON_RECOVERY",
                    evidence=None,
                )
                cancelled.append(str(row["message_id"]))
            return tuple(cancelled)

        result = self._transaction(operation)
        if not isinstance(result, tuple):
            raise PersistenceError("unexpected recovery result")
        return result

    def apply_broker_event(self, event: BrokerEvent) -> bool:
        processed_at = utc_now()

        def operation(connection: sqlite3.Connection) -> bool:
            duplicate = connection.execute(
                "SELECT 1 FROM processed_order_events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            if duplicate is not None:
                return False
            self._transition_order(
                connection,
                event.intent_id,
                event.new_state,
                event.occurred_at,
                broker_order_id=event.broker_order_id,
                realized_pnl_minor=event.realized_pnl_minor,
            )
            connection.execute(
                """
                INSERT INTO processed_order_events(
                    event_id, intent_id, new_state, occurred_at, processed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.intent_id,
                    event.new_state.value,
                    event.occurred_at.isoformat(),
                    processed_at.isoformat(),
                ),
            )
            if event.new_state in {OrderState.SETTLED, OrderState.REJECTED}:
                self._release_for_intent(
                    connection,
                    event.intent_id,
                    event.occurred_at,
                    reason=f"ORDER_{event.new_state.value}",
                    evidence=event.event_id,
                )
            return True

        return bool(self._transaction(operation))

    def apply_normalized_broker_event(
        self,
        event: BrokerOrderEvent,
    ) -> BrokerEventApplyResult:
        """Persist a durable inbox record and its financial effect in one transaction."""

        received_at = utc_now()

        def operation(connection: sqlite3.Connection) -> BrokerEventApplyResult:
            duplicate = connection.execute(
                """
                SELECT order_id, evidence_hash, processing_result
                FROM broker_order_events WHERE event_id = ?
                """,
                (event.event_id,),
            ).fetchone()
            if duplicate is not None:
                order_state = self._order_state_by_id(connection, duplicate["order_id"])
                if duplicate["evidence_hash"] == event.evidence_hash:
                    return BrokerEventApplyResult(
                        BrokerEventApplyStatus.DUPLICATE,
                        order_state,
                        None,
                    )
                connection.execute(
                    """
                    UPDATE broker_order_events
                    SET conflict_count = conflict_count + 1,
                        last_conflicting_hash = ?, reason_code = ?
                    WHERE event_id = ?
                    """,
                    (
                        event.evidence_hash,
                        "BROKER_EVENT_REPLAY_CONFLICT",
                        event.event_id,
                    ),
                )
                return BrokerEventApplyResult(
                    BrokerEventApplyStatus.CONFLICT,
                    order_state,
                    "BROKER_EVENT_REPLAY_CONFLICT",
                )

            row = connection.execute(
                """
                SELECT o.order_id, o.intent_id, o.state, o.broker, o.account_id,
                       o.broker_order_id, o.correlation_id, o.realized_pnl_minor,
                       o.last_external_sequence,
                       ti.product, ti.symbol, ti.direction, ti.amount_minor, ti.currency
                FROM orders o
                JOIN trade_intents ti ON ti.intent_id = o.intent_id
                WHERE o.order_id = ?
                """,
                (event.client_order_ref,),
            ).fetchone()
            if row is None:
                self._insert_broker_order_event(
                    connection,
                    event,
                    None,
                    BrokerEventApplyStatus.CONFLICT.value,
                    "BROKER_EVENT_CLIENT_REFERENCE_MISMATCH",
                    received_at,
                )
                return BrokerEventApplyResult(
                    BrokerEventApplyStatus.CONFLICT,
                    None,
                    "BROKER_EVENT_CLIENT_REFERENCE_MISMATCH",
                )

            mismatch = self._broker_event_matching_conflict(row, event)
            if mismatch is not None:
                self._insert_broker_order_event(
                    connection,
                    event,
                    str(row["order_id"]),
                    BrokerEventApplyStatus.CONFLICT.value,
                    mismatch,
                    received_at,
                )
                return BrokerEventApplyResult(
                    BrokerEventApplyStatus.CONFLICT,
                    OrderState(str(row["state"])),
                    mismatch,
                )

            if event.external_sequence is not None:
                reused_sequence = connection.execute(
                    """
                    SELECT event_id FROM broker_order_events
                    WHERE order_id = ? AND external_sequence = ?
                    """,
                    (row["order_id"], event.external_sequence),
                ).fetchone()
                if reused_sequence is not None:
                    reason = "BROKER_EVENT_SEQUENCE_CONFLICT"
                    self._insert_broker_order_event(
                        connection,
                        event,
                        str(row["order_id"]),
                        BrokerEventApplyStatus.CONFLICT.value,
                        reason,
                        received_at,
                    )
                    return BrokerEventApplyResult(
                        BrokerEventApplyStatus.CONFLICT,
                        OrderState(str(row["state"])),
                        reason,
                    )

            target = self._order_state_for_external_status(event.external_status)
            current = OrderState(str(row["state"]))
            terminal_result = self._terminal_broker_event_result(row, event, current, target)
            if terminal_result is not None:
                self._insert_broker_order_event(
                    connection,
                    event,
                    str(row["order_id"]),
                    terminal_result.status.value,
                    terminal_result.reason_code,
                    received_at,
                )
                return terminal_result

            previous_sequence = row["last_external_sequence"]
            if (
                event.external_sequence is not None
                and previous_sequence is not None
                and event.external_sequence < int(previous_sequence)
            ):
                reason = "BROKER_EVENT_LATE"
                self._insert_broker_order_event(
                    connection,
                    event,
                    str(row["order_id"]),
                    BrokerEventApplyStatus.LATE_IGNORED.value,
                    reason,
                    received_at,
                )
                return BrokerEventApplyResult(
                    BrokerEventApplyStatus.LATE_IGNORED,
                    current,
                    reason,
                )
            if current is OrderState.OPEN and target is OrderState.ACCEPTED:
                reason = "BROKER_EVENT_LATE"
                self._insert_broker_order_event(
                    connection,
                    event,
                    str(row["order_id"]),
                    BrokerEventApplyStatus.LATE_IGNORED.value,
                    reason,
                    received_at,
                )
                return BrokerEventApplyResult(
                    BrokerEventApplyStatus.LATE_IGNORED,
                    current,
                    reason,
                )

            gap = event.external_sequence is not None and (
                (previous_sequence is None and event.external_sequence > 1)
                or (
                    previous_sequence is not None
                    and event.external_sequence > int(previous_sequence) + 1
                )
            )
            processing_status = (
                BrokerEventApplyStatus.APPLIED_WITH_GAP if gap else BrokerEventApplyStatus.APPLIED
            )
            reason_code = "ORDER_EVENT_SEQUENCE_GAP" if gap else None
            self._insert_broker_order_event(
                connection,
                event,
                str(row["order_id"]),
                processing_status.value,
                reason_code,
                received_at,
            )
            self._inject("after_broker_event_inbox")

            was_ambiguous = current in {
                OrderState.UNKNOWN,
                OrderState.SETTLEMENT_UNKNOWN,
                OrderState.MANUAL_REVIEW,
            }
            if was_ambiguous:
                self._transition_order(
                    connection,
                    str(row["intent_id"]),
                    OrderState.RECONCILING,
                    event.observed_at,
                )
                current = OrderState.RECONCILING
            if current is not target:
                try:
                    self._transition_order(
                        connection,
                        str(row["intent_id"]),
                        target,
                        event.occurred_at,
                        broker_order_id=event.broker_order_id,
                        realized_pnl_minor=event.result_minor,
                    )
                except InvalidOrderTransition:
                    connection.execute(
                        """
                        UPDATE broker_order_events
                        SET processing_result = ?, reason_code = ? WHERE event_id = ?
                        """,
                        (
                            BrokerEventApplyStatus.CONFLICT.value,
                            "BROKER_EVENT_STATE_CONFLICT",
                            event.event_id,
                        ),
                    )
                    return BrokerEventApplyResult(
                        BrokerEventApplyStatus.CONFLICT,
                        OrderState(str(row["state"])),
                        "BROKER_EVENT_STATE_CONFLICT",
                    )
            connection.execute(
                """
                UPDATE orders
                SET broker_order_id = COALESCE(broker_order_id, ?),
                    last_external_sequence = COALESCE(?, last_external_sequence),
                    last_broker_event_id = ?,
                    resolution_source = CASE WHEN ? THEN 'ORDER_EVENT' ELSE resolution_source END,
                    resolved_at = CASE WHEN ? THEN ? ELSE resolved_at END
                WHERE order_id = ?
                """,
                (
                    event.broker_order_id,
                    event.external_sequence,
                    event.event_id,
                    was_ambiguous,
                    was_ambiguous,
                    received_at.isoformat(),
                    row["order_id"],
                ),
            )
            if was_ambiguous:
                connection.execute(
                    """
                    UPDATE outbox_messages
                    SET state = ?, state_reason = ?,
                        dispatched_at = COALESCE(dispatched_at, ?)
                    WHERE intent_id = ? AND state = ?
                    """,
                    (
                        OutboxState.RECONCILED.value,
                        f"RECONCILED_EVENT_{event.external_status.value}",
                        received_at.isoformat(),
                        row["intent_id"],
                        OutboxState.AMBIGUOUS.value,
                    ),
                )
            if target in {OrderState.REJECTED, OrderState.SETTLED}:
                self._release_for_intent(
                    connection,
                    str(row["intent_id"]),
                    event.occurred_at,
                    reason=f"BROKER_EVENT_{target.value}",
                    evidence=event.event_id,
                )
            self._inject("before_broker_event_commit")
            return BrokerEventApplyResult(processing_status, target, reason_code)

        result = self._transaction(operation)
        if not isinstance(result, BrokerEventApplyResult):
            raise PersistenceError("unexpected broker event result")
        return result

    @staticmethod
    def _order_state_by_id(
        connection: sqlite3.Connection,
        order_id: str | None,
    ) -> OrderState | None:
        if order_id is None:
            return None
        row = connection.execute(
            "SELECT state FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        return OrderState(str(row["state"])) if row is not None else None

    @staticmethod
    def _broker_event_matching_conflict(
        row: sqlite3.Row,
        event: BrokerOrderEvent,
    ) -> str | None:
        comparisons = (
            (event.broker.value, row["broker"], "BROKER_EVENT_SCOPE_MISMATCH"),
            (event.account_id, row["account_id"], "BROKER_EVENT_ACCOUNT_MISMATCH"),
            (event.correlation_id, row["correlation_id"], "BROKER_EVENT_CORRELATION_MISMATCH"),
            (event.product, row["product"], "BROKER_EVENT_PRODUCT_MISMATCH"),
            (event.symbol, row["symbol"], "BROKER_EVENT_SYMBOL_MISMATCH"),
            (event.direction.value, row["direction"], "BROKER_EVENT_DIRECTION_MISMATCH"),
            (event.amount.minor_units, row["amount_minor"], "BROKER_EVENT_AMOUNT_MISMATCH"),
            (event.amount.currency, row["currency"], "BROKER_EVENT_CURRENCY_MISMATCH"),
        )
        for actual, expected, reason in comparisons:
            if actual != expected:
                return reason
        if row["broker_order_id"] is not None and row["broker_order_id"] != event.broker_order_id:
            return "BROKER_ORDER_ID_CONFLICT"
        if event.result_currency is not None and event.result_currency != row["currency"]:
            return "BROKER_EVENT_RESULT_CURRENCY_MISMATCH"
        return None

    @staticmethod
    def _order_state_for_external_status(status: ExternalOrderStatus) -> OrderState:
        states = {
            ExternalOrderStatus.ACCEPTED: OrderState.ACCEPTED,
            ExternalOrderStatus.OPEN: OrderState.OPEN,
            ExternalOrderStatus.SETTLED: OrderState.SETTLED,
            ExternalOrderStatus.SETTLEMENT_UNKNOWN: OrderState.SETTLEMENT_UNKNOWN,
            ExternalOrderStatus.REJECTED: OrderState.REJECTED,
        }
        try:
            return states[status]
        except KeyError as exc:
            raise PersistenceError("unsupported broker lifecycle status") from exc

    @staticmethod
    def _terminal_broker_event_result(
        row: sqlite3.Row,
        event: BrokerOrderEvent,
        current: OrderState,
        target: OrderState,
    ) -> BrokerEventApplyResult | None:
        if not current.is_terminal:
            return None
        if current is target:
            if current is OrderState.SETTLED and row["realized_pnl_minor"] != event.result_minor:
                return BrokerEventApplyResult(
                    BrokerEventApplyStatus.CONFLICT,
                    current,
                    "BROKER_EVENT_SETTLEMENT_CONFLICT",
                )
            return BrokerEventApplyResult(
                BrokerEventApplyStatus.LATE_IGNORED,
                current,
                "BROKER_EVENT_TERMINAL_DUPLICATE",
            )
        if current is OrderState.SETTLED and target in {OrderState.ACCEPTED, OrderState.OPEN}:
            return BrokerEventApplyResult(
                BrokerEventApplyStatus.LATE_IGNORED,
                current,
                "BROKER_EVENT_LATE",
            )
        return BrokerEventApplyResult(
            BrokerEventApplyStatus.CONFLICT,
            current,
            "BROKER_EVENT_STATE_CONFLICT",
        )

    @staticmethod
    def _insert_broker_order_event(
        connection: sqlite3.Connection,
        event: BrokerOrderEvent,
        order_id: str | None,
        processing_result: str,
        reason_code: str | None,
        created_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO broker_order_events(
                event_id, order_id, event_version, broker, account_id,
                client_order_ref, broker_order_id, correlation_id, external_sequence,
                external_status, occurred_at, observed_at, product, symbol, direction,
                amount_minor, currency, result_minor, result_currency, evidence_hash,
                processing_result, reason_code, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                order_id,
                event.event_version,
                event.broker.value,
                event.account_id,
                event.client_order_ref,
                event.broker_order_id,
                event.correlation_id,
                event.external_sequence,
                event.external_status.value,
                event.occurred_at.isoformat(),
                event.observed_at.isoformat(),
                event.product,
                event.symbol,
                event.direction.value,
                event.amount.minor_units,
                event.amount.currency,
                event.result_minor,
                event.result_currency,
                event.evidence_hash,
                processing_result,
                reason_code,
                created_at.isoformat(),
            ),
        )

    def begin_reconciliation_attempt(
        self,
        attempt_id: str,
        order_id: str,
        correlation_id: str,
        started_at: datetime | None = None,
    ) -> None:
        actual_started_at = started_at or utc_now()

        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                INSERT INTO reconciliation_attempts(
                    attempt_id, order_id, correlation_id, started_at, result
                ) VALUES (?, ?, ?, ?, 'STARTED')
                """,
                (
                    attempt_id,
                    order_id,
                    correlation_id,
                    actual_started_at.isoformat(),
                ),
            )

        self._transaction(operation)

    def complete_reconciliation_attempt(
        self,
        attempt_id: str,
        result: str,
        reason_code: str,
        completed_at: datetime | None = None,
    ) -> None:
        actual_completed_at = completed_at or utc_now()

        def operation(connection: sqlite3.Connection) -> None:
            changed = connection.execute(
                """
                UPDATE reconciliation_attempts
                SET completed_at = ?, result = ?, reason_code = ?
                WHERE attempt_id = ? AND result = 'STARTED'
                """,
                (
                    actual_completed_at.isoformat(),
                    result,
                    reason_code,
                    attempt_id,
                ),
            ).rowcount
            if changed != 1:
                raise PersistenceError("reconciliation attempt is not STARTED")

        self._transaction(operation)

    def apply_reconciliation_evidence(
        self,
        attempt_id: str,
        evidence: ReconciliationEvidence,
        resolved_at: datetime | None = None,
    ) -> ReconciliationApplyResult:
        actual_resolved_at = resolved_at or utc_now()

        def operation(connection: sqlite3.Connection) -> ReconciliationApplyResult:
            row = connection.execute(
                """
                SELECT o.order_id, o.intent_id, o.state, o.broker, o.account_id,
                       o.broker_order_id, o.resolution_evidence_id, o.correlation_id,
                       ti.product, ti.symbol, ti.direction, ti.amount_minor, ti.currency
                FROM reconciliation_attempts ra
                JOIN orders o ON o.order_id = ra.order_id
                JOIN trade_intents ti ON ti.intent_id = o.intent_id
                WHERE ra.attempt_id = ? AND ra.result = 'STARTED'
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise PersistenceError("reconciliation attempt is not STARTED")
            canonical_hash = self._evidence_hash(evidence)
            existing = connection.execute(
                """
                SELECT canonical_hash, order_id
                FROM reconciliation_evidence WHERE evidence_id = ?
                """,
                (evidence.evidence_id,),
            ).fetchone()
            if existing is not None and (
                existing["canonical_hash"] != canonical_hash
                or existing["order_id"] != row["order_id"]
            ):
                return self._finish_reconciliation_conflict(
                    connection,
                    attempt_id,
                    row,
                    actual_resolved_at,
                    "RECONCILIATION_EVIDENCE_CONFLICT",
                )
            if existing is None:
                self._insert_reconciliation_evidence(
                    connection,
                    str(row["order_id"]),
                    evidence,
                    canonical_hash,
                    actual_resolved_at,
                )
                self._inject("after_reconciliation_evidence")
            mismatch_reason = self._matching_conflict_reason(row, evidence)
            if mismatch_reason is not None:
                return self._finish_reconciliation_conflict(
                    connection,
                    attempt_id,
                    row,
                    actual_resolved_at,
                    mismatch_reason,
                    evidence_id=evidence.evidence_id,
                )
            previous_evidence_id = row["resolution_evidence_id"]
            if previous_evidence_id is not None:
                previous = connection.execute(
                    """
                    SELECT external_status, canonical_hash
                    FROM reconciliation_evidence WHERE evidence_id = ?
                    """,
                    (previous_evidence_id,),
                ).fetchone()
                if previous is None:
                    raise PersistenceError("order references missing reconciliation evidence")
                if previous_evidence_id == evidence.evidence_id:
                    self._finish_reconciliation_attempt(
                        connection,
                        attempt_id,
                        "IDEMPOTENT",
                        None,
                        actual_resolved_at,
                        evidence.evidence_id,
                    )
                    return ReconciliationApplyResult(
                        ReconciliationApplyStatus.IDEMPOTENT,
                        OrderState(str(row["state"])),
                        None,
                    )
                if previous["external_status"] != evidence.external_status.value:
                    return self._finish_reconciliation_conflict(
                        connection,
                        attempt_id,
                        row,
                        actual_resolved_at,
                        "RECONCILIATION_EVIDENCE_CONFLICT",
                        evidence_id=evidence.evidence_id,
                    )
            if evidence.external_status is ExternalOrderStatus.EXTERNAL_UNKNOWN:
                self._finish_reconciliation_attempt(
                    connection,
                    attempt_id,
                    "UNRESOLVED",
                    "RECONCILIATION_EXTERNAL_UNKNOWN",
                    actual_resolved_at,
                    evidence.evidence_id,
                )
                return ReconciliationApplyResult(
                    ReconciliationApplyStatus.UNRESOLVED,
                    OrderState(str(row["state"])),
                    "RECONCILIATION_EXTERNAL_UNKNOWN",
                )
            target_states = {
                ExternalOrderStatus.ACCEPTED: OrderState.ACCEPTED,
                ExternalOrderStatus.REJECTED: OrderState.REJECTED,
                ExternalOrderStatus.OPEN: OrderState.OPEN,
                ExternalOrderStatus.SETTLED: OrderState.SETTLED,
                ExternalOrderStatus.SETTLEMENT_UNKNOWN: OrderState.SETTLEMENT_UNKNOWN,
            }
            target = target_states[evidence.external_status]
            current = OrderState(str(row["state"]))
            if current in {OrderState.UNKNOWN, OrderState.SETTLEMENT_UNKNOWN}:
                self._transition_order(
                    connection,
                    str(row["intent_id"]),
                    OrderState.RECONCILING,
                    actual_resolved_at,
                )
                current = OrderState.RECONCILING
            if current is not target:
                self._transition_order(
                    connection,
                    str(row["intent_id"]),
                    target,
                    actual_resolved_at,
                    broker_order_id=evidence.broker_order_id,
                    realized_pnl_minor=evidence.realized_pnl_minor,
                )
            connection.execute(
                """
                UPDATE orders
                SET resolution_source = ?, resolution_evidence_id = ?, resolved_at = ?,
                    broker_order_id = COALESCE(?, broker_order_id)
                WHERE order_id = ?
                """,
                (
                    evidence.source.value,
                    evidence.evidence_id,
                    actual_resolved_at.isoformat(),
                    evidence.broker_order_id,
                    row["order_id"],
                ),
            )
            connection.execute(
                """
                UPDATE outbox_messages
                SET state = ?, state_reason = ?, dispatched_at = COALESCE(dispatched_at, ?)
                WHERE intent_id = ? AND state = ?
                """,
                (
                    OutboxState.RECONCILED.value,
                    f"RECONCILED_{evidence.external_status.value}",
                    actual_resolved_at.isoformat(),
                    row["intent_id"],
                    OutboxState.AMBIGUOUS.value,
                ),
            )
            if target in {OrderState.REJECTED, OrderState.SETTLED}:
                self._release_for_intent(
                    connection,
                    str(row["intent_id"]),
                    actual_resolved_at,
                    reason=f"RECONCILED_{target.value}",
                    evidence=evidence.evidence_id,
                )
            self._finish_reconciliation_attempt(
                connection,
                attempt_id,
                "RESOLVED",
                None,
                actual_resolved_at,
                evidence.evidence_id,
            )
            self._inject("before_reconciliation_commit")
            return ReconciliationApplyResult(ReconciliationApplyStatus.RESOLVED, target, None)

        result = self._transaction(operation)
        if not isinstance(result, ReconciliationApplyResult):
            raise PersistenceError("unexpected reconciliation result")
        return result

    @staticmethod
    def _evidence_hash(evidence: ReconciliationEvidence) -> str:
        payload = json.dumps(evidence.to_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _insert_reconciliation_evidence(
        connection: sqlite3.Connection,
        order_id: str,
        evidence: ReconciliationEvidence,
        canonical_hash: str,
        received_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO reconciliation_evidence(
                evidence_id, order_id, source, observed_at, client_order_ref,
                broker_order_id, external_status, broker, account_id, product, symbol,
                direction, amount_minor, currency, realized_pnl_minor, raw_reference_hash,
                evidence_version, canonical_hash, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence.evidence_id,
                order_id,
                evidence.source.value,
                evidence.observed_at.isoformat(),
                evidence.client_order_ref,
                evidence.broker_order_id,
                evidence.external_status.value,
                evidence.broker.value,
                evidence.account_id,
                evidence.product,
                evidence.symbol,
                evidence.direction.value,
                evidence.amount.minor_units,
                evidence.amount.currency,
                evidence.realized_pnl_minor,
                evidence.raw_reference_hash,
                evidence.evidence_version,
                canonical_hash,
                received_at.isoformat(),
            ),
        )

    @staticmethod
    def _matching_conflict_reason(
        row: sqlite3.Row,
        evidence: ReconciliationEvidence,
    ) -> str | None:
        comparisons = (
            (evidence.client_order_ref, row["order_id"], "CLIENT_ORDER_REF_CONFLICT"),
            (evidence.broker.value, row["broker"], "BROKER_CONFLICT"),
            (evidence.account_id, row["account_id"], "ACCOUNT_CONFLICT"),
            (evidence.product, row["product"], "PRODUCT_CONFLICT"),
            (evidence.symbol, row["symbol"], "SYMBOL_CONFLICT"),
            (evidence.direction.value, row["direction"], "DIRECTION_CONFLICT"),
            (evidence.amount.minor_units, row["amount_minor"], "AMOUNT_CONFLICT"),
            (evidence.amount.currency, row["currency"], "CURRENCY_CONFLICT"),
        )
        for actual, expected, reason in comparisons:
            if actual != expected:
                return reason
        if (
            row["broker_order_id"] is not None
            and evidence.broker_order_id is not None
            and row["broker_order_id"] != evidence.broker_order_id
        ):
            return "BROKER_ORDER_ID_CONFLICT"
        return None

    @classmethod
    def _finish_reconciliation_conflict(
        cls,
        connection: sqlite3.Connection,
        attempt_id: str,
        row: sqlite3.Row,
        completed_at: datetime,
        reason_code: str,
        *,
        evidence_id: str | None = None,
    ) -> ReconciliationApplyResult:
        cls._finish_reconciliation_attempt(
            connection,
            attempt_id,
            "CONFLICT",
            reason_code,
            completed_at,
            evidence_id,
        )
        return ReconciliationApplyResult(
            ReconciliationApplyStatus.CONFLICT,
            OrderState(str(row["state"])),
            reason_code,
        )

    @staticmethod
    def _finish_reconciliation_attempt(
        connection: sqlite3.Connection,
        attempt_id: str,
        result: str,
        reason_code: str | None,
        completed_at: datetime,
        evidence_id: str | None,
    ) -> None:
        changed = connection.execute(
            """
            UPDATE reconciliation_attempts
            SET completed_at = ?, result = ?, reason_code = ?, evidence_id = ?
            WHERE attempt_id = ? AND result = 'STARTED'
            """,
            (
                completed_at.isoformat(),
                result,
                reason_code,
                evidence_id,
                attempt_id,
            ),
        ).rowcount
        if changed != 1:
            raise PersistenceError("reconciliation attempt is not STARTED")

    def release_reservation(self, reservation_id: str) -> None:
        released_at = utc_now()

        def operation(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                """
                SELECT rr.intent_id, rr.state AS reservation_state, o.state AS order_state
                FROM risk_reservations rr
                JOIN orders o ON o.intent_id = rr.intent_id
                WHERE rr.reservation_id = ?
                """,
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise PersistenceError("reservation not found")
            if row["reservation_state"] == RiskReservationState.RELEASED.value:
                return
            if row["order_state"] not in {OrderState.SETTLED.value, OrderState.REJECTED.value}:
                raise ReservationReleaseBlocked(
                    "active or ambiguous exposure requires proven terminal state"
                )
            self._release_for_intent(
                connection,
                row["intent_id"],
                released_at,
                reason="NORMAL_TERMINAL_RELEASE",
                evidence=None,
            )

        self._transaction(operation)

    def release_after_reconciliation(
        self,
        reservation_id: str,
        resolved_state: OrderState,
        evidence_reference: str,
    ) -> None:
        if not evidence_reference.strip():
            raise ValueError("reconciliation evidence is required")
        if resolved_state not in {OrderState.SETTLED, OrderState.REJECTED}:
            raise ValueError("reconciliation must prove a terminal state before release")
        released_at = utc_now()

        def operation(connection: sqlite3.Connection) -> None:
            row = connection.execute(
                """
                SELECT rr.intent_id, o.state AS order_state
                FROM risk_reservations rr
                JOIN orders o ON o.intent_id = rr.intent_id
                WHERE rr.reservation_id = ?
                """,
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise PersistenceError("reservation not found")
            current_state = OrderState(row["order_state"])
            if current_state in {OrderState.UNKNOWN, OrderState.SETTLEMENT_UNKNOWN}:
                self._transition_order(
                    connection,
                    row["intent_id"],
                    OrderState.RECONCILING,
                    released_at,
                )
            elif current_state is not OrderState.RECONCILING:
                raise ReservationReleaseBlocked(
                    "only ambiguous orders can use the reconciliation release path"
                )
            self._transition_order(
                connection,
                row["intent_id"],
                resolved_state,
                released_at,
            )
            self._release_for_intent(
                connection,
                row["intent_id"],
                released_at,
                reason="EXPLICIT_RECONCILIATION",
                evidence=evidence_reference,
            )

        self._transaction(operation)

    def _transition_order(
        self,
        connection: sqlite3.Connection,
        intent_id: str,
        new_state: OrderState,
        changed_at: datetime,
        *,
        broker_order_id: str | None = None,
        realized_pnl_minor: int | None = None,
    ) -> None:
        row = connection.execute(
            "SELECT state FROM orders WHERE intent_id = ?", (intent_id,)
        ).fetchone()
        if row is None:
            raise PersistenceError("order not found")
        current = OrderState(row["state"])
        if current == new_state:
            return
        if new_state not in ALLOWED_TRANSITIONS[current]:
            raise InvalidOrderTransition(f"cannot transition order from {current} to {new_state}")
        connection.execute(
            """
            UPDATE orders
            SET state = ?, broker_order_id = COALESCE(?, broker_order_id),
                realized_pnl_minor = COALESCE(?, realized_pnl_minor),
                pnl_application_count = pnl_application_count + ?, updated_at = ?
            WHERE intent_id = ?
            """,
            (
                new_state.value,
                broker_order_id,
                realized_pnl_minor,
                int(new_state is OrderState.SETTLED and realized_pnl_minor is not None),
                changed_at.isoformat(),
                intent_id,
            ),
        )

    @staticmethod
    def _release_for_intent(
        connection: sqlite3.Connection,
        intent_id: str,
        released_at: datetime,
        *,
        reason: str,
        evidence: str | None,
    ) -> None:
        connection.execute(
            """
            UPDATE risk_reservations
            SET state = ?, released_at = ?, release_reason = ?, reconciliation_evidence = ?
                , release_count = release_count + 1
            WHERE intent_id = ? AND state = ?
            """,
            (
                RiskReservationState.RELEASED.value,
                released_at.isoformat(),
                reason,
                evidence,
                intent_id,
                RiskReservationState.ACTIVE.value,
            ),
        )


class FinancialUnitOfWork:
    def __init__(self, writer: SingleDatabaseWriter) -> None:
        self._writer = writer

    def persist(
        self,
        *,
        request: OrderRequest,
        command: OrderCommand,
        intent_id: str,
        reservation_id: str,
        order_id: str,
        created_at: datetime,
        global_max_exposure_minor_units: int | None = None,
        max_exposure_per_symbol_minor_units: int | None = None,
    ) -> None:
        self._writer.persist_intent_reservation_outbox(
            request=request,
            command=command,
            intent_id=intent_id,
            reservation_id=reservation_id,
            order_id=order_id,
            created_at=created_at,
            global_max_exposure_minor_units=global_max_exposure_minor_units,
            max_exposure_per_symbol_minor_units=max_exposure_per_symbol_minor_units,
        )
