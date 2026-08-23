from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.core.coordinator import OrderCoordinator
from apps.core.health import HealthGate
from apps.core.runtime import CoreRuntime
from apps.simulated_worker.worker import SimulatedWorker
from packages.domain.models import (
    Broker,
    Direction,
    Money,
    OrderCommand,
    OrderRequest,
    WorkerOutcome,
    utc_now,
)
from packages.observability.events import InMemoryEventSink
from packages.persistence.backup import DatabaseBackupService
from packages.persistence.database import (
    IntegrityCheckMode,
    open_reader_connection,
    open_writer_connection,
    verify_database_integrity,
)
from packages.persistence.health import (
    DatabaseFailureReason,
    DatabaseHealth,
    DatabaseHealthStatus,
)
from packages.persistence.migrations import MIGRATIONS, Migration, apply_migrations
from packages.persistence.reader import StateReader
from packages.persistence.writer import (
    DatabaseStartupError,
    PersistenceError,
    SingleDatabaseWriter,
)


def make_request(
    suffix: str,
    *,
    account_id: str | None = None,
    deadline_at: datetime | None = None,
) -> OrderRequest:
    return OrderRequest(
        correlation_id=f"correlation-{suffix}",
        broker=Broker.DERIV,
        account_id=account_id or f"demo-account-{suffix}",
        product="DIGITAL_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1_000, "USD"),
        strategy_id="storage-test-strategy",
        strategy_version="1.0.0",
        deadline_at=deadline_at or datetime.now(UTC) + timedelta(minutes=5),
    )


def persist_with_ids(
    writer: SingleDatabaseWriter,
    suffix: str,
    *,
    intent_id: str | None = None,
    reservation_id: str | None = None,
    message_id: str | None = None,
    order_id: str | None = None,
) -> None:
    request = make_request(suffix)
    actual_intent_id = intent_id or f"intent-{suffix}"
    actual_message_id = message_id or f"message-{suffix}"
    command = OrderCommand(
        message_id=actual_message_id,
        correlation_id=request.correlation_id,
        intent_id=actual_intent_id,
        order_id=order_id or f"order-{suffix}",
        broker=request.broker,
        account_id=request.account_id,
        product=request.product,
        symbol=request.symbol,
        direction=request.direction,
        amount=request.amount,
        deadline_at=request.deadline_at,
    )
    writer.persist_intent_reservation_outbox(
        request=request,
        command=command,
        intent_id=actual_intent_id,
        reservation_id=reservation_id or f"reservation-{suffix}",
        order_id=order_id or f"order-{suffix}",
        created_at=utc_now(),
    )


def test_corruption_is_detected_and_core_fails_closed(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    database_path = profile / "state.db"
    writer = SingleDatabaseWriter(database_path)
    writer.close()
    database_path.write_bytes(b"controlled invalid sqlite test file")

    events = InMemoryEventSink()
    runtime = CoreRuntime(profile, event_sink=events)
    with pytest.raises(DatabaseStartupError) as captured:
        runtime.start()

    assert captured.value.reason_code == "DB_INTEGRITY_FAILED"
    assert runtime.database_health.state.status is DatabaseHealthStatus.FAILED
    assert runtime.database_health.state.reason is DatabaseFailureReason.DB_INTEGRITY_FAILED
    assert runtime.health_gate.state.is_open is False
    assert runtime.dispatcher_started is False
    assert database_path.read_bytes() == b"controlled invalid sqlite test file"
    assert any(event.event_name == "database_failure" for event in events.events)


def test_expected_database_missing_is_not_silently_recreated(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    database_path = profile / "state.db"
    writer = SingleDatabaseWriter(database_path)
    writer.close()
    database_path.unlink()

    runtime = CoreRuntime(profile)
    with pytest.raises(DatabaseStartupError) as captured:
        runtime.start()
    assert captured.value.reason_code == "DB_MISSING_UNEXPECTED"
    assert runtime.dispatcher_started is False
    assert not database_path.exists()


def test_write_failure_updates_database_health_and_blocks_dispatch(tmp_path: Path) -> None:
    database_health = DatabaseHealth()

    def fail_write(stage: str) -> None:
        if stage == "before_commit":
            raise OSError("deterministic simulated disk full")

    writer = SingleDatabaseWriter(
        tmp_path / "state.db",
        fault_injector=fail_write,
        database_health=database_health,
    )
    gate = HealthGate(database_health)
    worker = SimulatedWorker()
    coordinator = OrderCoordinator(writer, worker, gate)

    with pytest.raises(PersistenceError):
        coordinator.submit(make_request("write-failure"))

    assert worker.received == []
    assert database_health.state.status is DatabaseHealthStatus.FAILED
    assert database_health.state.reason is DatabaseFailureReason.DB_WRITE_FAILED
    assert gate.state.reason_code == "DB_WRITE_FAILED"
    writer.close()


def test_query_only_connection_rejects_writes_and_keeps_foreign_keys_enabled(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(database_path)
    connection = open_reader_connection(database_path)
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "INSERT INTO schema_migrations VALUES (999, 'invalid', 'now', 'invalid')"
            )
    finally:
        connection.close()
        writer.close()


def test_foreign_key_is_enforced_on_every_writer_connection(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(database_path)
    connection = open_writer_connection(database_path)
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO risk_reservations(
                    reservation_id, intent_id, broker, account_id, amount_minor,
                    currency, state, created_at
                ) VALUES ('orphan', 'missing', 'DERIV', 'demo', 100, 'USD', 'ACTIVE', 'now')
                """
            )
    finally:
        connection.close()
        writer.close()


@pytest.mark.parametrize(
    "duplicate_field",
    ["intent_id", "reservation_id", "message_id", "order_id"],
)
def test_financial_identifiers_have_database_unique_constraints(
    tmp_path: Path, duplicate_field: str
) -> None:
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    persist_with_ids(writer, "original")
    duplicate_values = {
        "intent_id": None,
        "reservation_id": None,
        "message_id": None,
        "order_id": None,
    }
    duplicate_values[duplicate_field] = f"{duplicate_field.removesuffix('_id')}-original"

    with pytest.raises(PersistenceError):
        persist_with_ids(writer, "duplicate", **duplicate_values)

    reader = StateReader(tmp_path / "state.db")
    assert reader.count("trade_intents") == 1
    assert reader.count("risk_reservations") == 1
    assert reader.count("outbox_messages") == 1
    assert reader.count("orders") == 1
    writer.close()


def test_migration_checksum_mismatch_blocks_startup(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(database_path)
    writer.close()
    connection = open_writer_connection(database_path)
    connection.execute("UPDATE schema_migrations SET checksum = 'modified' WHERE version = 1")
    connection.close()

    database_health = DatabaseHealth()
    with pytest.raises(DatabaseStartupError) as captured:
        SingleDatabaseWriter(database_path, database_health=database_health)
    assert captured.value.reason_code == "MIGRATION_CHECKSUM_MISMATCH"
    assert database_health.state.reason is DatabaseFailureReason.DB_MIGRATION_FAILED


def test_failed_migration_rolls_back_and_prevents_financial_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(database_path)
    writer.close()
    broken = Migration(
        version=999,
        name="0999_broken_test_migration",
        statements=(
            "CREATE TABLE must_rollback(id INTEGER PRIMARY KEY)",
            "INSERT INTO table_that_does_not_exist VALUES (1)",
        ),
    )

    def apply_broken_migration(connection: sqlite3.Connection) -> None:
        apply_migrations(connection, MIGRATIONS + (broken,))

    monkeypatch.setattr(
        "packages.persistence.writer.apply_migrations",
        apply_broken_migration,
    )
    runtime = CoreRuntime(tmp_path)
    with pytest.raises(DatabaseStartupError) as captured:
        runtime.start()
    assert captured.value.reason_code == "DB_MIGRATION_FAILED"
    assert runtime.dispatcher_started is False
    assert runtime.database_health.state.reason is DatabaseFailureReason.DB_MIGRATION_FAILED

    connection = open_writer_connection(database_path)
    assert (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'must_rollback'"
        ).fetchone()
        is None
    )
    assert (
        connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 999").fetchone()[
            0
        ]
        == 0
    )
    connection.close()


def test_backup_preserves_ambiguous_state_and_is_independently_consistent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(database_path)
    reader = StateReader(database_path)
    worker = SimulatedWorker([WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND])
    coordinator = OrderCoordinator(writer, worker, HealthGate())
    persisted = coordinator.submit(make_request("backup-unknown"))
    backup_path = tmp_path / "backups" / "snapshot.db"

    DatabaseBackupService(writer).create_backup(backup_path)

    backup_connection = sqlite3.connect(backup_path)
    try:
        verify_database_integrity(backup_connection, IntegrityCheckMode.FULL)
    finally:
        backup_connection.close()
    backup_reader = StateReader(backup_path)
    assert backup_reader.count("schema_migrations") == len(MIGRATIONS)
    assert backup_reader.one("orders", "order_id", persisted.order_id)["state"] == "UNKNOWN"
    assert (
        backup_reader.one("outbox_messages", "message_id", persisted.message_id)["state"]
        == "AMBIGUOUS"
    )
    assert (
        backup_reader.one("risk_reservations", "reservation_id", persisted.reservation_id)["state"]
        == "ACTIVE"
    )
    assert reader.count("trade_intents") == backup_reader.count("trade_intents")
    writer.close()


def test_backup_while_source_has_active_wal_includes_latest_commit(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(database_path)
    persist_with_ids(writer, "wal-backup")
    wal_path = database_path.with_name("state.db-wal")
    assert wal_path.exists() and wal_path.stat().st_size > 0

    backup_path = tmp_path / "active-wal-backup.db"
    DatabaseBackupService(writer).create_backup(backup_path)
    backup_reader = StateReader(backup_path)
    assert backup_reader.count("trade_intents") == 1
    assert backup_reader.count("outbox_messages") == 1
    writer.close()


def test_recovery_cancels_only_never_claimed_expired_message(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    writer = SingleDatabaseWriter(profile / "state.db")
    worker = SimulatedWorker()
    coordinator = OrderCoordinator(writer, worker, HealthGate())
    expired = make_request(
        "expired-recovery",
        deadline_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    persisted = coordinator.submit(expired, dispatch=False)
    writer.close()

    runtime = CoreRuntime(profile, worker)
    report = runtime.start()
    try:
        assert report.expired_message_ids == (persisted.message_id,)
        outbox = runtime.reader.one("outbox_messages", "message_id", persisted.message_id)
        reservation = runtime.reader.one(
            "risk_reservations", "reservation_id", persisted.reservation_id
        )
        assert outbox is not None and outbox["state"] == "CANCELLED"
        assert outbox["state_reason"] == "CANCELLED_EXPIRED"
        assert reservation is not None and reservation["state"] == "RELEASED"
        assert worker.received == []
    finally:
        runtime.shutdown()


def test_normal_shutdown_releases_profile_guard(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    runtime = CoreRuntime(profile)
    runtime.start()
    assert runtime.dispatcher_started is True
    runtime.shutdown()

    replacement = CoreRuntime(profile)
    replacement.start()
    replacement.shutdown()


def test_shutdown_and_restart_preserve_unknown_exposure(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    runtime = CoreRuntime(
        profile,
        SimulatedWorker([WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND]),
    )
    runtime.start()
    persisted = runtime.submit(make_request("shutdown-unknown"))
    runtime.shutdown()

    restarted = CoreRuntime(profile)
    restarted.start()
    try:
        order = restarted.reader.one("orders", "order_id", persisted.order_id)
        reservation = restarted.reader.one(
            "risk_reservations", "reservation_id", persisted.reservation_id
        )
        outbox = restarted.reader.one("outbox_messages", "message_id", persisted.message_id)
        assert order is not None and order["state"] == "UNKNOWN"
        assert reservation is not None and reservation["state"] == "ACTIVE"
        assert outbox is not None and outbox["state"] == "AMBIGUOUS"
        assert restarted.dispatcher_started is False
    finally:
        restarted.shutdown()


def test_single_core_guard_does_not_prevent_multiple_internal_accounts(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    worker = SimulatedWorker([WorkerOutcome.ACCEPTED, WorkerOutcome.ACCEPTED])
    runtime = CoreRuntime(profile, worker)
    runtime.start()
    try:
        runtime.submit(make_request("account-a", account_id="account-a"))
        runtime.submit(make_request("account-b", account_id="account-b"))
        assert len(worker.received) == 2
        assert runtime.reader.count("risk_reservations") == 2
    finally:
        runtime.shutdown()


def test_startup_backup_and_recovery_emit_structured_events(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    events = InMemoryEventSink()
    runtime = CoreRuntime(profile, event_sink=events)
    runtime.start()
    runtime.backup_service.create_backup(tmp_path / "observability-backup.db")
    runtime.shutdown()

    names = {event.event_name for event in events.events}
    assert {
        "core_instance_lock_acquired",
        "database_opened",
        "database_integrity_checked",
        "recovery_started",
        "recovery_completed",
        "database_backup_created",
    } <= names
