from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

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
from packages.domain.symbols import canonicalize_symbol
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
from packages.protocol.messages import NotFoundEvidence


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


class ManifestMonitorPendingError(PersistenceError):
    reason_code = "MANIFEST_MONITOR_PENDING"


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
        reference_currency: str | None = None,
    ) -> None:
        payload = json.dumps(command.to_payload(), sort_keys=True, separators=(",", ":"))

        def operation(connection: sqlite3.Connection) -> None:
            if reference_currency is not None and request.amount.currency != reference_currency:
                raise RiskLimitExceededError(
                    "HG_EXPOSURE_CURRENCY_MISMATCH",
                    "Reservation currency does not match the configured reference currency.",
                )
            if (
                request.manifest_context is not None
                and connection.execute(
                    "SELECT 1 FROM manifest_order_bindings b JOIN orders o USING(order_id) "
                    "WHERE b.consumed=0 AND o.state='SETTLED' LIMIT 1"
                ).fetchone()
                is not None
            ):
                # In the SAME writer transaction as admission: no new entry can
                # overtake a committed settlement awaiting its statistical gate.
                raise ManifestMonitorPendingError("MANIFEST_MONITOR_PENDING")
            if global_max_exposure_minor_units is not None:
                row = connection.execute(
                    "SELECT COALESCE(SUM(amount_minor), 0) AS total "
                    "FROM risk_reservations WHERE state = ? AND currency = ?",
                    (RiskReservationState.ACTIVE.value, request.amount.currency),
                ).fetchone()
                active_global = int(row["total"])
                if active_global + request.amount.minor_units > global_max_exposure_minor_units:
                    raise RiskLimitExceededError(
                        "HG_GLOBAL_EXPOSURE_EXCEEDED",
                        f"Active global exposure ({active_global + request.amount.minor_units}) "
                        f"exceeds limit ({global_max_exposure_minor_units})",
                    )
            if max_exposure_per_symbol_minor_units is not None:
                rows = connection.execute(
                    """
                    SELECT r.amount_minor, r.currency, t.symbol
                    FROM risk_reservations r
                    JOIN trade_intents t ON t.intent_id = r.intent_id
                    WHERE r.state = ? AND r.currency = ?
                    """,
                    (RiskReservationState.ACTIVE.value, request.amount.currency),
                ).fetchall()
                request_symbol = canonicalize_symbol(request.symbol)
                active_symbol = sum(
                    int(row["amount_minor"])
                    for row in rows
                    if canonicalize_symbol(str(row["symbol"])) == request_symbol
                )
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
            if request.manifest_context is not None:
                context = json.loads(request.manifest_context)
                if context["strategy_key"] != request.strategy_id:
                    raise ValueError("manifest binding does not match order")
                connection.execute(
                    "INSERT INTO manifest_order_bindings"
                    "(order_id, strategy_key, revision, context) "
                    "VALUES (?, ?, ?, ?)",
                    (order_id, request.strategy_id, context["revision"], request.manifest_context),
                )
            self._inject("before_commit")

        self._transaction(operation)

    def list_active_reservations(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT rr.reservation_id, rr.broker, rr.account_id,
                       rr.amount_minor, rr.currency, ti.symbol
                FROM risk_reservations rr
                JOIN trade_intents ti ON ti.intent_id = rr.intent_id
                WHERE rr.state = 'ACTIVE'
                ORDER BY rr.created_at, rr.reservation_id
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def configure_digit_risk_runtime(
        self,
        policy: Mapping[str, object],
        *,
        reset_active_sequence: bool = False,
    ) -> None:
        """Mirror the active digit policy into state.db without losing an active sequence."""

        required = {
            "config_fingerprint": str,
            "currency": str,
            "martingale_enabled": bool,
            "martingale_max_steps": int,
            "max_consecutive_losses": int,
            "cooldown_seconds": str,
        }
        for name, expected in required.items():
            value = policy.get(name)
            if type(value) is not expected:
                raise ValueError(f"invalid digit runtime policy field: {name}")
        fingerprint = str(policy["config_fingerprint"])
        currency = str(policy["currency"])
        max_steps = cast(int, policy["martingale_max_steps"])
        max_losses = cast(int, policy["max_consecutive_losses"])
        try:
            cooldown = Decimal(str(policy["cooldown_seconds"]))
        except InvalidOperation as exc:
            raise ValueError("invalid digit runtime cooldown") from exc
        if (
            not fingerprint
            or len(currency) != 3
            or not 1 <= max_steps <= 4
            or not 1 <= max_losses <= 5
            or not cooldown.is_finite()
            or cooldown <= 0
        ):
            raise ValueError("invalid digit runtime policy")
        now = utc_now().isoformat()

        def operation(connection: sqlite3.Connection) -> None:
            current = connection.execute(
                "SELECT config_fingerprint, martingale_step FROM digit_risk_runtime "
                "WHERE singleton_id = 1"
            ).fetchone()
            if (
                current is not None
                and current["config_fingerprint"] != fingerprint
                and int(current["martingale_step"]) > 0
                and not reset_active_sequence
            ):
                raise PersistenceError("DIGIT_MARTINGALE_SEQUENCE_ACTIVE")
            if current is None:
                connection.execute(
                    """
                    INSERT INTO digit_risk_runtime(
                        singleton_id, config_fingerprint, currency, martingale_enabled,
                        martingale_max_steps, max_consecutive_losses, cooldown_seconds,
                        updated_at
                    ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fingerprint,
                        currency,
                        int(bool(policy["martingale_enabled"])),
                        max_steps,
                        max_losses,
                        str(cooldown),
                        now,
                    ),
                )
            else:
                reset_clause = (
                    ", consecutive_losses = 0, martingale_step = 0, "
                    "pinned_symbol = NULL, cumulative_sequence_loss_minor = 0, "
                    "cooldown_started_at = NULL"
                    if reset_active_sequence
                    else ""
                )
                connection.execute(
                    f"""
                    UPDATE digit_risk_runtime
                    SET config_fingerprint = ?, currency = ?, martingale_enabled = ?,
                        martingale_max_steps = ?,
                        max_consecutive_losses = ?, cooldown_seconds = ?, updated_at = ?
                        {reset_clause}
                    WHERE singleton_id = 1
                    """,
                    (
                        fingerprint,
                        currency,
                        int(bool(policy["martingale_enabled"])),
                        max_steps,
                        max_losses,
                        str(cooldown),
                        now,
                    ),
                )

        self._transaction(operation)

    def reset_digit_test_session_if_flat(self) -> None:
        """Reset durable Demo risk counters only when no financial work is pending."""

        now = utc_now().isoformat()

        def operation(connection: sqlite3.Connection) -> None:
            pending = connection.execute(
                """
                SELECT 1
                FROM orders o
                JOIN trade_intents ti ON ti.intent_id = o.intent_id
                WHERE o.broker = 'DERIV'
                  AND ti.product IN (
                      'DIGITDIFF', 'DIGITOVER', 'DIGITUNDER', 'DIGITEVEN', 'DIGITODD'
                  )
                  AND o.state NOT IN ('SETTLED', 'REJECTED')
                LIMIT 1
                """
            ).fetchone()
            active_reservation = connection.execute(
                "SELECT 1 FROM risk_reservations WHERE state = 'ACTIVE' LIMIT 1"
            ).fetchone()
            if pending is not None or active_reservation is not None:
                raise PersistenceError("DIGIT_TEST_SESSION_RESET_BLOCKED_EXPOSURE")
            updated = connection.execute(
                """
                UPDATE digit_risk_runtime
                SET daily_pnl_minor = 0,
                    consecutive_losses = 0,
                    martingale_step = 0,
                    pinned_symbol = NULL,
                    cumulative_sequence_loss_minor = 0,
                    cooldown_started_at = NULL,
                    session_started_at = ?,
                    updated_at = ?
                WHERE singleton_id = 1
                """,
                (now, now),
            )
            if updated.rowcount != 1:
                raise PersistenceError("DIGIT_RISK_RUNTIME_NOT_CONFIGURED")

        self._transaction(operation)

    def expire_digit_cooldown(self, now: datetime | None = None) -> dict[str, Any] | None:
        """Atomically end an elapsed durable cooldown and return the current state."""

        checked_at = now or utc_now()
        if checked_at.tzinfo is None:
            raise ValueError("digit cooldown clock must be timezone-aware")

        def operation(connection: sqlite3.Connection) -> dict[str, Any] | None:
            row = connection.execute(
                "SELECT * FROM digit_risk_runtime WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                return None
            started_raw = row["cooldown_started_at"]
            if isinstance(started_raw, str):
                started = datetime.fromisoformat(started_raw)
                if started.tzinfo is None:
                    raise PersistenceError("digit cooldown timestamp is not timezone-aware")
                duration = Decimal(str(row["cooldown_seconds"]))
                elapsed = Decimal(str((checked_at - started).total_seconds()))
                if elapsed >= duration:
                    connection.execute(
                        """
                        UPDATE digit_risk_runtime
                        SET consecutive_losses = 0, martingale_step = 0,
                            pinned_symbol = NULL, cumulative_sequence_loss_minor = 0,
                            cooldown_started_at = NULL, updated_at = ?
                        WHERE singleton_id = 1
                        """,
                        (checked_at.isoformat(),),
                    )
                    row = connection.execute(
                        "SELECT * FROM digit_risk_runtime WHERE singleton_id = 1"
                    ).fetchone()
            return None if row is None else dict(row)

        result = self._transaction(operation)
        if result is not None and not isinstance(result, dict):
            raise PersistenceError("unexpected digit runtime result")
        return result

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
        reason_code: str | None = None,
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
                state_reason = None
            elif outcome == "REJECTED":
                outbox_state = OutboxState.DISPATCHED
                order_state = OrderState.REJECTED
                state_reason = reason_code or "BROKER_REJECTED"
            elif outcome == "TIMEOUT_AFTER_POSSIBLE_SEND":
                outbox_state = OutboxState.AMBIGUOUS
                order_state = OrderState.UNKNOWN
                state_reason = reason_code or "POSSIBLE_SEND_TIMEOUT"
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
                    state_reason,
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
            if (
                target is OrderState.SETTLED
                and event.result_minor is not None
                and event.result_currency is not None
            ):
                self._apply_digit_runtime_settlement(
                    connection,
                    product=event.product,
                    symbol=event.symbol,
                    currency=event.result_currency,
                    pnl_minor=event.result_minor,
                    order_id=str(row["order_id"]),
                    settlement_id=event.event_id,
                    occurred_at=event.occurred_at,
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
                previous_status = ExternalOrderStatus(str(previous["external_status"]))
                if (
                    previous_status is not evidence.external_status
                    and not self._is_valid_reconciliation_status_progression(
                        previous_status,
                        evidence.external_status,
                    )
                ):
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
            settlement_newly_applied = (
                target is OrderState.SETTLED and current is not OrderState.SETTLED
            )
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
            if settlement_newly_applied and evidence.realized_pnl_minor is not None:
                self._apply_digit_runtime_settlement(
                    connection,
                    product=str(row["product"]),
                    symbol=str(row["symbol"]),
                    currency=str(row["currency"]),
                    pnl_minor=evidence.realized_pnl_minor,
                    order_id=str(row["order_id"]),
                    settlement_id=evidence.evidence_id,
                    occurred_at=actual_resolved_at,
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
    def _is_valid_reconciliation_status_progression(
        previous: ExternalOrderStatus,
        current: ExternalOrderStatus,
    ) -> bool:
        """Permit only broker lifecycle progress; contradictions remain conflicts."""

        return current in {
            ExternalOrderStatus.ACCEPTED: {
                ExternalOrderStatus.OPEN,
                ExternalOrderStatus.SETTLEMENT_UNKNOWN,
                ExternalOrderStatus.SETTLED,
            },
            ExternalOrderStatus.OPEN: {
                ExternalOrderStatus.SETTLEMENT_UNKNOWN,
                ExternalOrderStatus.SETTLED,
            },
            ExternalOrderStatus.SETTLEMENT_UNKNOWN: {
                ExternalOrderStatus.SETTLED,
            },
        }.get(previous, set())

    def apply_reconciliation_not_found(
        self,
        attempt_id: str,
        evidence: NotFoundEvidence,
        *,
        not_found_grace_seconds: float,
        confirmation_interval_seconds: float,
        resolved_at: datetime | None = None,
    ) -> ReconciliationApplyResult:
        """Atomically finalize a proven never-submitted order and release its reservation."""

        if (
            not evidence.confirms_both_sources
            or not_found_grace_seconds <= 0
            or confirmation_interval_seconds <= 0
        ):
            raise ValueError("NOT_FOUND reconciliation proof is invalid")
        actual_resolved_at = resolved_at or utc_now()
        reason = "RECONCILIATION_NOT_FOUND_BOTH_SOURCES"

        def operation(connection: sqlite3.Connection) -> ReconciliationApplyResult:
            row = connection.execute(
                """
                SELECT o.order_id, o.intent_id, o.state, o.created_at,
                       o.resolution_source
                FROM reconciliation_attempts ra
                JOIN orders o ON o.order_id = ra.order_id
                WHERE ra.attempt_id = ? AND ra.result = 'STARTED'
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise PersistenceError("reconciliation attempt is not STARTED")
            current = OrderState(str(row["state"]))
            if current is OrderState.REJECTED and row["resolution_source"] == "STATUS_QUERY":
                self._finish_reconciliation_attempt(
                    connection,
                    attempt_id,
                    "IDEMPOTENT",
                    reason,
                    actual_resolved_at,
                    None,
                )
                return ReconciliationApplyResult(
                    ReconciliationApplyStatus.IDEMPOTENT,
                    current,
                    reason,
                )
            submitted_at = datetime.fromisoformat(str(row["created_at"]))
            age_seconds = (evidence.observed_at - submitted_at).total_seconds()
            prior = connection.execute(
                """
                SELECT attempt_id, completed_at
                FROM reconciliation_attempts
                WHERE order_id = ? AND attempt_id != ?
                  AND result = 'UNRESOLVED' AND reason_code = ?
                  AND completed_at IS NOT NULL
                ORDER BY completed_at DESC LIMIT 1
                """,
                (row["order_id"], attempt_id, reason),
            ).fetchone()
            confirmed = False
            if prior is not None:
                prior_completed = datetime.fromisoformat(str(prior["completed_at"]))
                confirmed = (
                    evidence.observed_at - prior_completed
                ).total_seconds() >= confirmation_interval_seconds
            if (
                current is not OrderState.UNKNOWN
                or age_seconds < not_found_grace_seconds
                or not confirmed
            ):
                self._finish_reconciliation_attempt(
                    connection,
                    attempt_id,
                    "UNRESOLVED",
                    reason,
                    evidence.observed_at,
                    None,
                )
                return ReconciliationApplyResult(
                    ReconciliationApplyStatus.UNRESOLVED,
                    current,
                    reason,
                )
            self._transition_order(
                connection,
                str(row["intent_id"]),
                OrderState.RECONCILING,
                actual_resolved_at,
            )
            self._transition_order(
                connection,
                str(row["intent_id"]),
                OrderState.REJECTED,
                actual_resolved_at,
            )
            connection.execute(
                """
                UPDATE orders
                SET resolution_source = 'STATUS_QUERY', resolved_at = ?
                WHERE order_id = ?
                """,
                (actual_resolved_at.isoformat(), row["order_id"]),
            )
            connection.execute(
                """
                UPDATE outbox_messages
                SET state = ?, state_reason = ?, dispatched_at = COALESCE(dispatched_at, ?)
                WHERE intent_id = ? AND state = ?
                """,
                (
                    OutboxState.RECONCILED.value,
                    "RECONCILED_NOT_FOUND",
                    actual_resolved_at.isoformat(),
                    row["intent_id"],
                    OutboxState.AMBIGUOUS.value,
                ),
            )
            evidence_reference = f"{reason}:{prior['attempt_id']}:{attempt_id}"
            self._release_for_intent(
                connection,
                str(row["intent_id"]),
                actual_resolved_at,
                reason="RECONCILED_NOT_FOUND",
                evidence=evidence_reference,
            )
            self._finish_reconciliation_attempt(
                connection,
                attempt_id,
                "RESOLVED",
                reason,
                actual_resolved_at,
                None,
            )
            self._inject("before_reconciliation_commit")
            return ReconciliationApplyResult(
                ReconciliationApplyStatus.RESOLVED,
                OrderState.REJECTED,
                reason,
            )

        result = self._transaction(operation)
        if not isinstance(result, ReconciliationApplyResult):
            raise PersistenceError("unexpected NOT_FOUND reconciliation result")
        return result

    @staticmethod
    def _apply_digit_runtime_settlement(
        connection: sqlite3.Connection,
        *,
        product: str,
        symbol: str,
        currency: str,
        pnl_minor: int,
        order_id: str,
        settlement_id: str,
        occurred_at: datetime,
    ) -> None:
        """Advance durable digit recovery state inside the settlement transaction."""

        if product.strip().upper() not in {
            "DIGITDIFF",
            "DIGITOVER",
            "DIGITUNDER",
            "DIGITEVEN",
            "DIGITODD",
        }:
            return
        runtime = connection.execute(
            "SELECT * FROM digit_risk_runtime WHERE singleton_id = 1"
        ).fetchone()
        if runtime is None:
            raise PersistenceError("digit risk runtime policy is not configured")
        if runtime["currency"] != currency:
            raise PersistenceError("digit settlement currency does not match runtime policy")
        # One order has exactly one terminal financial effect. Event streaming and
        # reconciliation may describe it with different evidence IDs.
        if runtime["last_order_id"] == order_id:
            return
        daily_pnl = int(runtime["daily_pnl_minor"]) + pnl_minor
        losses = int(runtime["consecutive_losses"])
        step = int(runtime["martingale_step"])
        cumulative = int(runtime["cumulative_sequence_loss_minor"])
        pinned_symbol: str | None = runtime["pinned_symbol"]
        cooldown_started_at: str | None = runtime["cooldown_started_at"]
        if pnl_minor < 0:
            losses += 1
            cumulative += -pnl_minor
            if bool(runtime["martingale_enabled"]):
                if step >= int(runtime["martingale_max_steps"]):
                    step = 0
                    cumulative = 0
                else:
                    step += 1
            else:
                step = 0
            pinned_symbol = symbol if step > 0 else None
            if losses >= int(runtime["max_consecutive_losses"]):
                cooldown_started_at = occurred_at.isoformat()
        elif pnl_minor > 0:
            losses = 0
            cumulative = max(0, cumulative - pnl_minor)
            if (
                bool(runtime["martingale_enabled"])
                and cumulative > 0
                and step < int(runtime["martingale_max_steps"])
            ):
                step += 1
            else:
                step = 0
                cumulative = 0
                pinned_symbol = None
            cooldown_started_at = None
        else:
            step = 0
            cumulative = 0
            pinned_symbol = None
            cooldown_started_at = None
        connection.execute(
            """
            UPDATE digit_risk_runtime
            SET daily_pnl_minor = ?, consecutive_losses = ?, martingale_step = ?,
                pinned_symbol = ?, cumulative_sequence_loss_minor = ?,
                cooldown_started_at = ?, last_order_id = ?, last_settlement_id = ?,
                updated_at = ?
            WHERE singleton_id = 1
            """,
            (
                daily_pnl,
                losses,
                step,
                pinned_symbol,
                cumulative,
                cooldown_started_at,
                order_id,
                settlement_id,
                occurred_at.isoformat(),
            ),
        )

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

    def load_iqoption_execution_state(self) -> dict[str, Any] | None:
        """One startup read, including evidence for a crash across submit.

        No order, reservation or outbox is changed by this projection.
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT state_json FROM iqoption_execution_state WHERE singleton=1"
            ).fetchone()
            if row is None:
                return None
            payload: dict[str, Any] = json.loads(row[0])
            pending = payload.get("pending")
            if pending is not None:
                rows = self._connection.execute(
                    """SELECT o.state, b.state_reason
                       FROM orders o JOIN outbox_messages b USING(intent_id)
                       WHERE o.correlation_id=? AND o.broker=? AND o.account_id=?""",
                    (pending["correlation_id"], "IQ_OPTION", "IQOPTION_PRACTICE"),
                ).fetchall()
                if len(rows) > 1:
                    raise PersistenceError("IQOPTION_CORRELATION_CONFLICT")
                payload["pending_evidence"] = None if not rows else dict(rows[0])
            return payload

    def save_iqoption_execution_state(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded) > 65536:
            raise PersistenceError("IQOPTION_EXECUTION_STATE_TOO_LARGE")

        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                """INSERT INTO iqoption_execution_state VALUES (1, ?)
                   ON CONFLICT(singleton) DO UPDATE SET state_json=excluded.state_json""",
                (encoded,),
            )

        self._transaction(operation)

    def manifest_monitor_states(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(row)
                for row in self._connection.execute(
                    "SELECT * FROM manifest_monitor_states"
                ).fetchall()
            ]

    def consume_manifest_orders(
        self,
        update: Callable[[dict[str, Any], dict[str, Any] | None, int], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Snapshot nonterminal bindings; atomically consume terminal evidence and SPRT.

        Called by the background monitor, never by the candle evaluation loop.
        A financial commit and a monitor commit may be separated by a crash:
        consumed=0 is the durable recovery cursor for either events or reconciliation.
        """

        def operation(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = connection.execute("""
                SELECT b.*, o.state, o.realized_pnl_minor, o.updated_at
                FROM manifest_order_bindings b JOIN orders o USING(order_id)
                WHERE b.consumed = 0 ORDER BY o.updated_at, o.order_id LIMIT 256
            """).fetchall()
            result = []
            for raw in rows:
                row = dict(raw)
                if row["state"] == "SETTLED":
                    if row["realized_pnl_minor"] is None:
                        raise ValueError("settled order without financial evidence")
                    prior = connection.execute(
                        "SELECT state_json FROM manifest_monitor_states WHERE revision = ?",
                        (row["revision"],),
                    ).fetchone()
                    binding = json.loads(row["context"])
                    prior_state = None if prior is None else json.loads(prior[0])
                    if prior_state is None:
                        legacy = connection.execute(
                            "SELECT * FROM sprt_monitors WHERE strategy_key = ?",
                            (row["strategy_key"],),
                        ).fetchone()
                        if legacy is not None and all(
                            Decimal(str(legacy[key])) == Decimal(str(binding[key]))
                            for key in ("p0", "p1")
                        ):
                            prior_state = dict(legacy)
                    updated = update(
                        binding,
                        prior_state,
                        int(row["realized_pnl_minor"]),
                    )
                    connection.execute(
                        "INSERT INTO manifest_monitor_states VALUES (?, ?, ?) "
                        "ON CONFLICT(revision) DO UPDATE SET state_json=excluded.state_json",
                        (row["revision"], row["strategy_key"], json.dumps(updated, sort_keys=True)),
                    )
                    row["monitor"] = updated
                if row["state"] in {"SETTLED", "REJECTED", "SEND_BLOCKED", "CANCELLED", "EXPIRED"}:
                    connection.execute(
                        "UPDATE manifest_order_bindings SET consumed=1 WHERE order_id=?",
                        (row["order_id"],),
                    )
                result.append(row)
            return result

        return cast(list[dict[str, Any]], self._transaction(operation))

    def save_sprt_monitor(
        self,
        strategy_key: str,
        p0: str,
        p1: str,
        alpha: str,
        beta: str,
        llr: str,
        n: int,
        wins: int,
        decision: str,
        status: str,
        updated_at: str,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO sprt_monitors (
                    strategy_key, p0, p1, alpha, beta, llr, n, wins, decision, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_key) DO UPDATE SET
                    p0 = excluded.p0,
                    p1 = excluded.p1,
                    alpha = excluded.alpha,
                    beta = excluded.beta,
                    llr = excluded.llr,
                    n = excluded.n,
                    wins = excluded.wins,
                    decision = excluded.decision,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    strategy_key,
                    p0,
                    p1,
                    alpha,
                    beta,
                    llr,
                    n,
                    wins,
                    decision,
                    status,
                    updated_at,
                ),
            )
            self._connection.commit()

    def get_sprt_monitor(self, strategy_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sprt_monitors WHERE strategy_key = ?",
                (strategy_key,),
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def list_sprt_monitors(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM sprt_monitors ORDER BY strategy_key ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def enqueue_outcome(
        self,
        strategy_key: str,
        ts: int,
        won: bool,
        payout_pct: str,
        created_at: str,
    ) -> int:
        with self._lock:
            cur = self._connection.execute(
                """
                INSERT INTO outcomes_queue (
                    strategy_key, ts, won, payout_pct, created_at, status
                ) VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (strategy_key, ts, 1 if won else 0, str(payout_pct), created_at),
            )
            self._connection.commit()
            return int(cur.lastrowid or 0)

    def fetch_pending_outcomes(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, strategy_key, ts, won, payout_pct, created_at
                FROM outcomes_queue
                WHERE status = 'pending'
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def ack_outcomes(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        with self._lock:
            placeholders = ",".join("?" for _ in ids)
            self._connection.execute(
                f"DELETE FROM outcomes_queue WHERE id IN ({placeholders})",
                tuple(ids),
            )
            self._connection.commit()

    def count_pending_outcomes(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS cnt FROM outcomes_queue WHERE status = 'pending'"
            ).fetchone()
            return int(row["cnt"]) if row else 0


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
        reference_currency: str | None = None,
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
            reference_currency=reference_currency,
        )
