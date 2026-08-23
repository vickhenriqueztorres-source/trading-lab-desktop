from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.audit import DecisionEventType, DecisionJournal
from packages.persistence.candle_repository import (
    CandleAppendResult,
    CandleConflictError,
    SqliteCandleRepository,
)
from packages.persistence.journal_repository import SqliteDecisionJournalRepository
from packages.persistence.strategy_data import (
    StrategyDataDatabase,
    StrategyDataIntegrityError,
    StrategyDataMigrationError,
)
from tests.replay.test_deterministic_replay import closed_candle


def test_strategy_data_db_is_separate_idempotent_and_rejects_candle_conflict(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="state.db"):
        StrategyDataDatabase(tmp_path / "state.db")

    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repository = SqliteCandleRepository(database)
    first = closed_candle(0, source_event_id="delivery-a")
    redelivery = closed_candle(0, source_event_id="delivery-b")
    conflicting = type(first)(
        broker=first.broker,
        symbol=first.symbol,
        timeframe_seconds=first.timeframe_seconds,
        open_time_ms=first.open_time_ms,
        close_time_ms=first.close_time_ms,
        open_units=first.open_units,
        high_units=first.high_units,
        low_units=first.low_units,
        close_units=first.close_units + 1,
        price_scale=first.price_scale,
        source=first.source,
        source_event_id="conflicting",
        source_timestamp_ms=first.source_timestamp_ms,
        received_timestamp_ms=first.received_timestamp_ms,
    )
    try:
        assert repository.store(first) is CandleAppendResult.STORED
        assert repository.store(redelivery) is CandleAppendResult.ALREADY_EXISTS
        with pytest.raises(CandleConflictError) as conflict:
            repository.store(conflicting)
        assert conflict.value.reason_code == "CANDLE_CONFLICT"
        assert repository.get(first.candle_id) == first
    finally:
        database.close()


def test_journal_is_append_only_and_detects_persisted_tampering(tmp_path: Path) -> None:
    path = tmp_path / "strategy_data.db"
    database = StrategyDataDatabase(path)
    repository = SqliteDecisionJournalRepository(database)
    journal = DecisionJournal("run-a", max_events=4)
    first = journal.append(
        DecisionEventType.CANDLE_ACCEPTED,
        occurred_at=datetime(2026, 8, 20, tzinfo=UTC),
        correlation_id="candle-a",
        causation_id=None,
        strategy_id="strategy-a",
        strategy_version="1.0.0",
        manifest_hash="a" * 64,
        configuration_hash="b" * 64,
        candle_id="c" * 64,
        payload=(("source", "FAKE"),),
    )
    second = journal.append(
        DecisionEventType.STRATEGY_EVALUATED,
        occurred_at=datetime(2026, 8, 20, tzinfo=UTC),
        correlation_id="candle-a",
        causation_id=first.event.event_id,
        strategy_id="strategy-a",
        strategy_version="1.0.0",
        manifest_hash="a" * 64,
        configuration_hash="b" * 64,
        candle_id="c" * 64,
        payload=(("reason", "NO_SIGNAL"),),
    )
    try:
        repository.append(first)
        repository.append(second)
        assert repository.verify_chain("run-a").valid
    finally:
        database.close()

    raw = sqlite3.connect(path)
    try:
        raw.execute(
            "UPDATE decision_events SET event_sha256 = ? WHERE run_id = ? AND sequence = 2",
            ("f" * 64, "run-a"),
        )
        raw.commit()
    finally:
        raw.close()
    reopened = StrategyDataDatabase(path)
    try:
        verification = SqliteDecisionJournalRepository(reopened).verify_chain("run-a")
        assert not verification.valid
        assert verification.reason_code == "JOURNAL_HASH_CHAIN_INVALID"
    finally:
        reopened.close()


def test_strategy_data_migration_checksum_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "strategy_data.db"
    database = StrategyDataDatabase(path)
    database.close()
    raw = sqlite3.connect(path)
    try:
        raw.execute("UPDATE strategy_schema_migrations SET checksum = ?", ("0" * 64,))
        raw.commit()
    finally:
        raw.close()

    with pytest.raises(StrategyDataMigrationError):
        StrategyDataDatabase(path)


def test_strategy_data_corruption_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "strategy_data.db"
    path.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(StrategyDataIntegrityError):
        StrategyDataDatabase(path)
