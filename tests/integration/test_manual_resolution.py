from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from packages.domain.models import (
    Broker,
    Direction,
    Money,
    OrderCommand,
    OrderRequest,
    OrderState,
    WorkerOutcome,
    utc_now,
)
from packages.persistence.database import open_writer_connection
from packages.persistence.migrations import apply_migrations
from packages.persistence.reader import StateReader
from packages.persistence.writer import PersistenceError, SingleDatabaseWriter
from packages.protocol.errors import ProtocolErrorCode


def _init_db(tmp_path: Path) -> tuple[SingleDatabaseWriter, StateReader]:
    db_path = tmp_path / "state.db"
    conn = open_writer_connection(db_path)
    apply_migrations(conn)
    conn.close()
    writer = SingleDatabaseWriter(db_path)
    reader = StateReader(db_path)
    return writer, reader


def _create_sample_order(
    writer: SingleDatabaseWriter,
    order_id: str,
    *,
    amount_minor: int = 1000,
    symbol: str = "EURUSD",
) -> None:
    intent_id = f"intent-{order_id}"
    req = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_1",
        strategy_id="test-strategy",
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        symbol=symbol,
        direction=Direction.CALL,
        amount=Money(amount_minor, "USD"),
        deadline_at=utc_now() + timedelta(seconds=60),
    )
    cmd = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=req.correlation_id,
        intent_id=intent_id,
        order_id=order_id,
        broker=req.broker,
        account_id=req.account_id,
        product=req.product,
        symbol=req.symbol,
        direction=req.direction,
        amount=req.amount,
        deadline_at=req.deadline_at,
    )
    writer.persist_intent_reservation_outbox(
        request=req,
        command=cmd,
        intent_id=intent_id,
        reservation_id=f"res-{order_id}",
        order_id=order_id,
        created_at=utc_now(),
    )
    writer.claim_next_message()
    writer.record_dispatch_result(
        cmd,
        WorkerOutcome.ACCEPTED.value,
        broker_order_id=f"broker-{order_id}",
    )


def test_record_reconciliation_conflict_transitions_to_manual_review(tmp_path: Path) -> None:
    writer, reader = _init_db(tmp_path)
    order_id = "ord-conflict-1"
    _create_sample_order(writer, order_id)

    # Order is initially ACCEPTED
    order_row = reader.one("orders", "order_id", order_id)
    assert order_row is not None
    assert order_row["state"] == OrderState.ACCEPTED.value

    # Record conflict
    writer.record_reconciliation_conflict(
        order_id=order_id,
        attempt_id=None,
        reason_code="IQOPTION_SYMBOL_MISMATCH",
        details="Discrepancy detected during verification",
    )

    # State must now be MANUAL_REVIEW with incremented version
    updated_row = reader.one("orders", "order_id", order_id)
    assert updated_row is not None
    assert updated_row["state"] == OrderState.MANUAL_REVIEW.value
    assert int(updated_row["version"]) >= 2

    # Manual review orders query must include it
    manual_orders = reader.list_manual_review_orders()
    assert any(str(r["order_id"]) == order_id for r in manual_orders)


def test_resolve_with_broker_evidence_settles_and_updates_audit(tmp_path: Path) -> None:
    writer, reader = _init_db(tmp_path)
    order_id = "ord-settle-1"
    _create_sample_order(writer, order_id, amount_minor=1500)

    # Move to MANUAL_REVIEW
    writer.record_reconciliation_conflict(
        order_id=order_id,
        attempt_id=None,
        reason_code="MANUAL_REVIEW_TRIGGERED",
        details="Triggered for test",
    )
    row_before = reader.one("orders", "order_id", order_id)
    assert row_before is not None
    v_before = int(row_before["version"])

    # Resolve order as SETTLED with profit
    writer.resolve_with_broker_evidence(
        order_id=order_id,
        expected_version=v_before,
        idempotency_key="idemp-settle-1",
        broker_order_id="1489201948",
        realized_pnl_minor=1275,
        operator_id="OPERATOR_ALICE",
        reason="Verified win in broker trading history",
    )

    row_after = reader.one("orders", "order_id", order_id)
    assert row_after is not None
    assert row_after["state"] == OrderState.SETTLED.value
    assert row_after["realized_pnl_minor"] == 1275
    assert row_after["manual_resolution_operator"] == "OPERATOR_ALICE"
    assert row_after["resolution_source"] == "MANUAL_OPERATOR"

    # Idempotent replay with the same idempotency key must succeed without error
    writer.resolve_with_broker_evidence(
        order_id=order_id,
        expected_version=v_before,
        idempotency_key="idemp-settle-1",
        broker_order_id="1489201948",
        realized_pnl_minor=1275,
        operator_id="OPERATOR_ALICE",
        reason="Verified win in broker trading history",
    )


def test_confirm_not_executed_rejects_and_updates_audit(tmp_path: Path) -> None:
    writer, reader = _init_db(tmp_path)
    order_id = "ord-reject-1"
    _create_sample_order(writer, order_id, amount_minor=2000)

    writer.record_reconciliation_conflict(
        order_id=order_id,
        attempt_id=None,
        reason_code="MANUAL_REVIEW_TRIGGERED",
        details="Triggered for reject test",
    )
    row_before = reader.one("orders", "order_id", order_id)
    assert row_before is not None
    v_before = int(row_before["version"])

    # Confirm not executed -> REJECTED
    writer.confirm_not_executed(
        order_id=order_id,
        expected_version=v_before,
        idempotency_key="idemp-reject-1",
        operator_id="OPERATOR_BOB",
        reason="Verified order never opened on IQ Option server",
    )

    row_after = reader.one("orders", "order_id", order_id)
    assert row_after is not None
    assert row_after["state"] == OrderState.REJECTED.value
    assert row_after["manual_resolution_operator"] == "OPERATOR_BOB"
    assert row_after["resolution_source"] == "MANUAL_NOT_EXECUTED"


def test_cas_version_conflict_is_prevented(tmp_path: Path) -> None:
    writer, reader = _init_db(tmp_path)
    order_id = "ord-cas-1"
    _create_sample_order(writer, order_id)

    writer.record_reconciliation_conflict(
        order_id=order_id,
        attempt_id=None,
        reason_code="MANUAL_REVIEW_TRIGGERED",
        details="Triggered for CAS test",
    )
    row_before = reader.one("orders", "order_id", order_id)
    assert row_before is not None
    actual_version = int(row_before["version"])

    # Attempt to resolve with stale expected_version
    stale_version = actual_version - 1
    with pytest.raises(PersistenceError) as exc_info:
        writer.resolve_with_broker_evidence(
            order_id=order_id,
            expected_version=stale_version,
            idempotency_key="idemp-cas-stale",
            broker_order_id="111",
            realized_pnl_minor=500,
            operator_id="OPERATOR_ALICE",
            reason="Testing CAS rejection",
        )
    assert ProtocolErrorCode.IQOPTION_MANUAL_REVIEW_STALE_VERSION.value in str(exc_info.value)


def test_idempotency_payload_mismatch_is_rejected(tmp_path: Path) -> None:
    writer, reader = _init_db(tmp_path)
    order_id = "ord-mismatch-1"
    _create_sample_order(writer, order_id, amount_minor=1000)

    writer.record_reconciliation_conflict(
        order_id=order_id,
        attempt_id=None,
        reason_code="MANUAL_REVIEW_TRIGGERED",
        details="Triggered for mismatch test",
    )
    row_before = reader.one("orders", "order_id", order_id)
    assert row_before is not None
    v_before = int(row_before["version"])

    # First resolution: PnL = 500
    writer.resolve_with_broker_evidence(
        order_id=order_id,
        expected_version=v_before,
        idempotency_key="idemp-key-fixed",
        broker_order_id="1001",
        realized_pnl_minor=500,
        operator_id="OP_1",
        reason="Initial settlement",
    )

    # Replay with same idempotency key but different PnL -> MUST fail with mismatch error
    with pytest.raises(PersistenceError) as exc_info:
        writer.resolve_with_broker_evidence(
            order_id=order_id,
            expected_version=v_before,
            idempotency_key="idemp-key-fixed",
            broker_order_id="1001",
            realized_pnl_minor=999,  # Mismatch!
            operator_id="OP_1",
            reason="Different settlement",
        )
    assert "IQOPTION_RESOLUTION_PAYLOAD_MISMATCH" in str(exc_info.value)


def test_migration_13_recovers_legacy_accepted_with_conflict(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_state.db"
    conn = open_writer_connection(db_path)
    # Apply up to migration 12
    from packages.persistence.migrations import MIGRATIONS, apply_migrations

    legacy_migrations = tuple(m for m in MIGRATIONS if m.version < 13)
    apply_migrations(conn, migrations=legacy_migrations)

    # Insert a legacy intent and order in ACCEPTED with a CONFLICT attempt (the 5f945c50 bug)
    legacy_order_id = "5f945c50-legacy"
    conn.execute(
        """
        INSERT INTO trade_intents (
            intent_id, correlation_id, broker, account_id, product, symbol,
            direction, amount_minor, currency, status, created_at, strategy_id, strategy_version
        ) VALUES (
            'intent-legacy', 'corr-legacy', 'IQ_OPTION', 'PRACTICE_1', 'BINARY_OPTION',
            'EURUSD-OTC', 'CALL', 1000, 'USD', 'DISPATCHED', '2026-09-15T00:00:00Z',
            'strat-1', '1.0.0'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO risk_reservations (
            reservation_id, intent_id, broker, account_id, amount_minor,
            currency, state, created_at
        ) VALUES (
            'res-legacy', 'intent-legacy', 'IQ_OPTION', 'PRACTICE_1', 1000,
            'USD', 'ACTIVE', '2026-09-15T00:00:00Z'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO orders (
            order_id, intent_id, broker, account_id, state, correlation_id,
            created_at, updated_at, version
        ) VALUES (
            ?, 'intent-legacy', 'IQ_OPTION', 'PRACTICE_1', 'ACCEPTED', 'corr-legacy',
            '2026-09-15T00:00:00Z', '2026-09-15T00:00:00Z', 1
        )
        """,
        (legacy_order_id,),
    )
    conn.execute(
        """
        INSERT INTO reconciliation_attempts (
            attempt_id, order_id, correlation_id, started_at, completed_at,
            result, reason_code
        ) VALUES (
            'att-legacy-1', ?, 'corr-legacy', '2026-09-15T00:01:00Z',
            '2026-09-15T00:01:05Z', 'CONFLICT', 'IQOPTION_SYMBOL_MISMATCH'
        )
        """,
        (legacy_order_id,),
    )
    conn.commit()
    conn.close()

    # Now run apply_migrations (which includes Migration 13)
    conn = open_writer_connection(db_path)
    apply_migrations(conn)
    conn.close()

    reader = StateReader(db_path)
    migrated_order = reader.one("orders", "order_id", legacy_order_id)
    assert migrated_order is not None
    assert migrated_order["state"] == OrderState.MANUAL_REVIEW.value
    assert migrated_order["resolution_source"] == "HISTORICAL_CONFLICT_RECOVERY"
    assert int(migrated_order["version"]) >= 2

    # The order must now appear in reader.list_manual_review_orders()
    manual_orders = reader.list_manual_review_orders()
    assert any(str(r["order_id"]) == legacy_order_id for r in manual_orders)


def test_recovery_command_and_gate_recalculation(tmp_path: Path) -> None:
    from apps.core.health import HealthGate
    from apps.core.reconciliation import ReconciliationCoordinator
    from apps.core.recovery_command import execute_manual_settlement
    from packages.observability.events import NullEventSink

    writer, reader = _init_db(tmp_path)
    order_id = "ord-gate-1"
    _create_sample_order(writer, order_id, amount_minor=1000)

    # Health gate has HG_RECONCILIATION_CONFLICT blocked
    gate = HealthGate()
    gate.block("HG_RECONCILIATION_CONFLICT")
    assert "HG_RECONCILIATION_CONFLICT" in gate.get_snapshot().active_blockers

    coordinator = ReconciliationCoordinator(
        writer,
        reader,
        None,  # type: ignore[arg-type]
        gate,
        NullEventSink(),
    )

    # While order is stuck, recalculate_gates preserves the blocker
    writer.record_reconciliation_conflict(order_id, None, "CONFLICT_TEST")
    coordinator.recalculate_gates()
    assert "HG_RECONCILIATION_CONFLICT" in gate.get_snapshot().active_blockers

    # Execute manual settlement via recovery command
    res = execute_manual_settlement(
        writer,
        reader,
        order_id=order_id,
        broker_order_id="BROKER-12345",
        realized_pnl_minor=850,
        operator="TEST_OPERATOR",
        reason="Verified WIN",
    )
    assert res["success"] is True
    assert res["resolved_state"] == "SETTLED"

    # Now reader.list_manual_review_orders() is empty
    assert len(reader.list_manual_review_orders()) == 0

    # Recalculate gates -> HG_RECONCILIATION_CONFLICT must be cleared!
    coordinator.recalculate_gates()
    assert "HG_RECONCILIATION_CONFLICT" not in gate.get_snapshot().active_blockers


def test_migration_14_auto_resolves_false_account_conflicts(tmp_path: Path) -> None:
    from packages.persistence.migrations import MIGRATIONS, apply_migrations

    db_path = tmp_path / "migration14_test.db"
    legacy_migrations = tuple(m for m in MIGRATIONS if m.version <= 13)

    conn = open_writer_connection(db_path)
    apply_migrations(conn, migrations=legacy_migrations)

    # Insert an IQ Option order stuck in MANUAL_REVIEW due to false ACCOUNT_CONFLICT
    order_id = "iq-stuck-false-conflict-1"
    intent_id = "intent-stuck-1"
    res_id = "res-stuck-1"
    now_iso = "2026-09-16T05:06:23.000000+00:00"

    conn.execute(
        """
        INSERT INTO trade_intents (
            intent_id, correlation_id, broker, account_id, product, symbol,
            direction, amount_minor, currency, status, created_at,
            strategy_id, strategy_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            intent_id,
            "corr-1",
            "IQ_OPTION",
            "IQOPTION_PRACTICE",
            "BINARY_OPTION",
            "COTTON-OTC",
            "CALL",
            100,
            "USD",
            "CREATED",
            now_iso,
            "rsi_test",
            "1.0.0",
        ),
    )
    conn.execute(
        """
        INSERT INTO orders (
            order_id, intent_id, broker, account_id, broker_order_id,
            state, correlation_id, created_at, updated_at, resolution_source, version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            intent_id,
            "IQ_OPTION",
            "IQOPTION_PRACTICE",
            "14266588536",
            "MANUAL_REVIEW",
            "corr-1",
            now_iso,
            now_iso,
            "RECONCILIATION_CONFLICT",
            2,
        ),
    )
    conn.execute(
        """
        INSERT INTO risk_reservations (
            reservation_id, intent_id, broker, account_id, amount_minor,
            currency, state, created_at, release_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (res_id, intent_id, "IQ_OPTION", "IQOPTION_PRACTICE", 100, "USD", "ACTIVE", now_iso, 0),
    )
    conn.execute(
        """
        INSERT INTO reconciliation_attempts (
            attempt_id, order_id, correlation_id, started_at, completed_at,
            result, reason_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("att-1", order_id, "corr-1", now_iso, now_iso, "CONFLICT", "ACCOUNT_CONFLICT"),
    )
    conn.commit()
    conn.close()

    # Now run apply_migrations with Migration 14 included
    conn = open_writer_connection(db_path)
    apply_migrations(conn)
    conn.close()

    reader = StateReader(db_path)
    order = reader.one("orders", "order_id", order_id)
    assert order is not None
    assert order["state"] == OrderState.REJECTED.value
    assert order["resolution_source"] == "FALSE_ACCOUNT_CONFLICT_AUTO_RESOLVED"
    assert order["manual_resolution_operator"] == "MIGRATION_0014"

    # Risk reservation must be RELEASED
    res = reader.one("risk_reservations", "reservation_id", res_id)
    assert res is not None
    assert res["state"] == "RELEASED"
    assert res["release_reason"] == "MIGRATION_0014_FALSE_CONFLICT_RELEASED"

    # Reconciliation attempt must be RESOLVED
    att = reader.one("reconciliation_attempts", "attempt_id", "att-1")
    assert att is not None
    assert att["result"] == "RESOLVED"
    assert att["reason_code"] == "FALSE_CONFLICT_REPAIRED"

    # Must no longer be in list_manual_review_orders
    manual = reader.list_manual_review_orders()
    assert not any(str(r["order_id"]) == order_id for r in manual)
