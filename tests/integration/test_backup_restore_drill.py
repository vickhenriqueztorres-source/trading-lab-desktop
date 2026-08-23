from __future__ import annotations

import hashlib
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apps.core.runtime import CoreRuntime
from apps.simulated_worker.worker import SimulatedWorker
from packages.domain.models import Broker, Direction, Money, OrderRequest
from packages.persistence.database import (
    IntegrityCheckMode,
    mark_database_expected,
    verify_database_integrity,
)
from packages.persistence.migrations import MIGRATIONS
from packages.persistence.reader import StateReader


def _request() -> OrderRequest:
    return OrderRequest(
        correlation_id="restore-drill-correlation",
        broker=Broker.DERIV,
        account_id="restore-drill-demo-account",
        product="DIGITAL_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1_000, "USD"),
        strategy_id="restore-drill-strategy",
        strategy_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _migration_rows(path: Path) -> tuple[tuple[object, ...], ...]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        return tuple(tuple(row) for row in rows)
    finally:
        connection.close()


def test_backup_restore_drill_recovers_committed_state_without_touching_original(
    tmp_path: Path,
) -> None:
    source_profile = tmp_path / "source-profile"
    backup_path = tmp_path / "backups" / "state-backup.db"
    runtime = CoreRuntime(source_profile, SimulatedWorker())
    runtime.start()
    persisted = runtime.submit(_request(), dispatch=False)
    runtime.backup_service.create_backup(backup_path)
    runtime.shutdown()

    source_database = source_profile / "state.db"
    source_reader = StateReader(source_database)
    expected_rows = {
        "intent": source_reader.one("trade_intents", "intent_id", persisted.intent_id),
        "reservation": source_reader.one(
            "risk_reservations", "reservation_id", persisted.reservation_id
        ),
        "outbox": source_reader.one("outbox_messages", "message_id", persisted.message_id),
        "order": source_reader.one("orders", "order_id", persisted.order_id),
    }
    expected_migrations = _migration_rows(source_database)
    source_digest = _sha256(source_database)
    offline_source = source_profile / "state.db.disaster-offline"
    source_database.replace(offline_source)

    restore_profile = tmp_path / "restored-profile"
    restored_database = restore_profile / "state.db"
    restore_profile.mkdir()
    restored_runtime: CoreRuntime | None = None
    try:
        shutil.copy2(backup_path, restored_database)
        mark_database_expected(restored_database)
        connection = sqlite3.connect(restored_database)
        try:
            assert verify_database_integrity(connection, IntegrityCheckMode.QUICK).is_healthy
            assert verify_database_integrity(connection, IntegrityCheckMode.FULL).is_healthy
        finally:
            connection.close()

        restored_runtime = CoreRuntime(restore_profile, SimulatedWorker())
        restored_runtime.start()
        assert restored_runtime.reader.count("schema_migrations") == len(MIGRATIONS)
        assert _migration_rows(restored_database) == expected_migrations
        assert (
            restored_runtime.reader.one("trade_intents", "intent_id", persisted.intent_id)
            == expected_rows["intent"]
        )
        assert (
            restored_runtime.reader.one(
                "risk_reservations", "reservation_id", persisted.reservation_id
            )
            == expected_rows["reservation"]
        )
        assert (
            restored_runtime.reader.one("outbox_messages", "message_id", persisted.message_id)
            == expected_rows["outbox"]
        )
        assert (
            restored_runtime.reader.one("orders", "order_id", persisted.order_id)
            == expected_rows["order"]
        )
        assert not source_database.exists()
        assert _sha256(offline_source) == source_digest
    finally:
        if restored_runtime is not None:
            restored_runtime.shutdown()
        offline_source.replace(source_database)

    assert _sha256(source_database) == source_digest
    assert (source_profile / "state.db.expected").is_file()
