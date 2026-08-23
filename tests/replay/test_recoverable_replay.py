from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import fields, replace
from pathlib import Path

import pytest

from packages.domain.models import Broker, Direction, Money
from packages.persistence.candle_repository import SqliteCandleRepository
from packages.persistence.journal_repository import (
    JournalConflictError,
    SqliteDecisionJournalRepository,
)
from packages.persistence.replay_repository import SqliteReplayRepository
from packages.persistence.strategy_commit_repository import (
    SqliteCandleDecisionCommitRepository,
)
from packages.persistence.strategy_data import StrategyDataDatabase
from packages.persistence.warmup_repository import SqliteWarmupRepository
from packages.replay import (
    CheckpointRestoreError,
    CheckpointRestoreReason,
    ReplayEngine,
    ReplayPersistence,
    ReplayRequest,
    configuration_hash_for,
)
from packages.strategies import RuntimeContext, RuntimePhase, StrategyStateV1, WarmupCheckpoint
from packages.strategy_catalog import StrategyCatalog, ValidationRegistry
from tests.helpers.strategy_fixtures import register_released
from tests.replay.test_deterministic_replay import closed_candle

STRATEGY_ID = "recoverable-warmup"


def catalog_factory() -> StrategyCatalog:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    register_released(
        catalog,
        registry,
        STRATEGY_ID,
        Direction.CALL,
        warmup_candles=400,
    )
    return catalog


def recoverable_request() -> ReplayRequest:
    candles = tuple(closed_candle(index) for index in range(500))
    manifest = catalog_factory().get(STRATEGY_ID, "1.0.0").manifest
    config_hash = configuration_hash_for("recoverable-config-1", ())
    return ReplayRequest(
        strategy_id=STRATEGY_ID,
        strategy_version="1.0.0",
        broker=Broker.DERIV,
        account_id="demo-recovery-account",
        product="DIGITAL_OPTION",
        symbol="EURUSD",
        timeframe_seconds=60,
        configuration_version="recoverable-config-1",
        parameters=(),
        configuration_hash=config_hash,
        manifest_hash=hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
        entitled_packs=frozenset({"phase0-candidates"}),
        requested_amount=Money(100, "USD"),
        strategy_remaining=Money(100, "USD"),
        account_remaining=Money(100, "USD"),
        global_remaining=Money(100, "USD"),
        candles=candles,
    )


def persistence_for(database: StrategyDataDatabase) -> ReplayPersistence:
    return ReplayPersistence(
        candles=SqliteCandleRepository(database),
        journal=SqliteDecisionJournalRepository(database),
        replays=SqliteReplayRepository(database),
        warmup=SqliteWarmupRepository(database),
        commits=SqliteCandleDecisionCommitRepository(database),
    )


def test_warmup_restores_deterministically_after_restart(tmp_path: Path) -> None:
    request = recoverable_request()
    path = tmp_path / "strategy_data.db"
    first_db = StrategyDataDatabase(path)
    first_persistence = persistence_for(first_db)
    first_session = ReplayEngine(catalog_factory).create_session(
        request,
        persistence=first_persistence,
    )
    first_session.process_many(request.candles[:300])
    checkpoint = first_session.checkpoint()
    assert checkpoint.runtime_phase is RuntimePhase.WARMING_UP
    first_db.close()

    second_db = StrategyDataDatabase(path)
    second_persistence = persistence_for(second_db)
    restored_checkpoint = second_persistence.warmup.latest(request.context)
    assert restored_checkpoint == checkpoint
    second_session = ReplayEngine(catalog_factory).create_session(
        request,
        persistence=second_persistence,
        checkpoint=restored_checkpoint,
    )
    second_session.process_many(request.candles[299:])
    resumed_result = second_session.complete()
    resumed_final_checkpoint = second_session.checkpoint()
    persisted_record = second_persistence.replays.get(request.run_id)
    journal_size_before_rerun = len(second_persistence.journal.events_for_run(request.run_id))
    rerun_result = ReplayEngine(catalog_factory).run(
        request,
        persistence=second_persistence,
    )
    journal_size_after_rerun = len(second_persistence.journal.events_for_run(request.run_id))
    second_db.close()

    clean_session = ReplayEngine(catalog_factory).create_session(request)
    clean_session.process_many(request.candles)
    clean_result = clean_session.complete()
    clean_checkpoint = clean_session.checkpoint()

    assert resumed_result == clean_result
    assert resumed_final_checkpoint.state_sha256 == clean_checkpoint.state_sha256
    assert resumed_result.final_hash == clean_result.final_hash
    assert rerun_result == resumed_result
    assert journal_size_after_rerun == journal_size_before_rerun
    assert persisted_record is not None
    assert persisted_record.result_sha256 == resumed_result.result_sha256


def test_candle_decisions_and_checkpoint_roll_back_as_one_unit(tmp_path: Path) -> None:
    request = recoverable_request()

    def fail_before_commit(point: str) -> None:
        if point == "before_strategy_candle_commit":
            raise RuntimeError("INJECTED_BEFORE_STRATEGY_CANDLE_COMMIT")

    database = StrategyDataDatabase(
        tmp_path / "strategy_data.db",
        fault_injector=fail_before_commit,
    )
    persistence = persistence_for(database)
    session = ReplayEngine(catalog_factory).create_session(
        request,
        persistence=persistence,
    )
    try:
        with pytest.raises(RuntimeError, match="INJECTED_BEFORE_STRATEGY_CANDLE_COMMIT"):
            session.process(request.candles[0])
        assert persistence.candles.get(request.candles[0].candle_id) == request.candles[0]
        assert persistence.journal.events_for_run(request.run_id) == ()
        assert persistence.warmup.latest(request.context) is None
    finally:
        database.close()


def test_identical_checkpoint_can_prove_distinct_replay_runs(tmp_path: Path) -> None:
    first_request = recoverable_request()
    second_request = replace(
        first_request,
        requested_amount=Money(200, "USD"),
    )
    assert second_request.run_id != first_request.run_id
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    persistence = persistence_for(database)
    try:
        first = ReplayEngine(catalog_factory).create_session(
            first_request,
            persistence=persistence,
        )
        first.process(first_request.candles[0])
        second = ReplayEngine(catalog_factory).create_session(
            second_request,
            persistence=persistence,
        )
        second.process(second_request.candles[0])

        assert persistence.journal.events_for_run(first_request.run_id)
        assert persistence.journal.events_for_run(second_request.run_id)
        assert first.checkpoint() == second.checkpoint()
    finally:
        database.close()


def _tampered_checkpoint(
    checkpoint: WarmupCheckpoint,
    **changes: object,
) -> WarmupCheckpoint:
    tampered = object.__new__(WarmupCheckpoint)
    for item in fields(WarmupCheckpoint):
        object.__setattr__(
            tampered, item.name, changes.get(item.name, getattr(checkpoint, item.name))
        )
    return tampered


def test_modified_checkpoint_is_rejected_with_stable_reason(tmp_path: Path) -> None:
    request = recoverable_request()
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    persistence = persistence_for(database)
    session = ReplayEngine(catalog_factory).create_session(request, persistence=persistence)
    session.process_many(request.candles[:3])
    checkpoint = session.checkpoint()
    tampered = _tampered_checkpoint(checkpoint, created_at_ms=checkpoint.created_at_ms + 1)
    try:
        with pytest.raises(CheckpointRestoreError) as rejected:
            ReplayEngine(catalog_factory).create_session(
                request,
                persistence=persistence,
                checkpoint=tampered,
            )
        assert rejected.value.reason is CheckpointRestoreReason.HASH_INVALID
    finally:
        database.close()


def test_checkpoint_state_version_is_rejected(tmp_path: Path) -> None:
    request = recoverable_request()
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    persistence = persistence_for(database)
    session = ReplayEngine(catalog_factory).create_session(request, persistence=persistence)
    session.process_many(request.candles[:3])
    checkpoint = session.checkpoint()
    unsupported_state = object.__new__(StrategyStateV1)
    object.__setattr__(unsupported_state, "candle_ids", checkpoint.state.candle_ids)
    object.__setattr__(unsupported_state, "candles_seen", checkpoint.state.candles_seen)
    object.__setattr__(unsupported_state, "version", 2)
    provisional = _tampered_checkpoint(checkpoint, state=unsupported_state)
    unsupported = _tampered_checkpoint(
        provisional,
        checkpoint_sha256=provisional.compute_sha256(),
    )
    try:
        with pytest.raises(CheckpointRestoreError) as rejected:
            ReplayEngine(catalog_factory).create_session(
                request,
                persistence=persistence,
                checkpoint=unsupported,
            )
        assert rejected.value.reason is CheckpointRestoreReason.STATE_VERSION_UNSUPPORTED
    finally:
        database.close()


@pytest.mark.parametrize(
    ("field_name", "replacement", "expected"),
    (
        ("manifest_sha256", "f" * 64, CheckpointRestoreReason.MANIFEST_MISMATCH),
        ("config_sha256", "e" * 64, CheckpointRestoreReason.CONFIG_MISMATCH),
        ("strategy_version", "2.0.0", CheckpointRestoreReason.CONTEXT_MISMATCH),
    ),
)
def test_checkpoint_from_other_identity_is_rejected(
    tmp_path: Path,
    field_name: str,
    replacement: str,
    expected: CheckpointRestoreReason,
) -> None:
    request = recoverable_request()
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    persistence = persistence_for(database)
    session = ReplayEngine(catalog_factory).create_session(request, persistence=persistence)
    session.process_many(request.candles[:3])
    original = session.checkpoint()
    context = RuntimeContext(
        strategy_id=original.strategy_id,
        strategy_version=(
            replacement if field_name == "strategy_version" else original.strategy_version
        ),
        broker=Broker(original.broker),
        account_id=original.account_id,
        product=original.product,
        symbol=original.symbol,
        timeframe_seconds=original.timeframe_seconds,
        configuration_version=original.configuration_version,
    )
    changed = WarmupCheckpoint.create(
        context,
        manifest_sha256=(
            replacement if field_name == "manifest_sha256" else original.manifest_sha256
        ),
        config_sha256=(replacement if field_name == "config_sha256" else original.config_sha256),
        runtime_phase=original.runtime_phase,
        state=original.state,
        last_close_time_ms=original.last_close_time_ms,
        created_at_ms=original.created_at_ms,
    )
    try:
        with pytest.raises(CheckpointRestoreError) as rejected:
            ReplayEngine(catalog_factory).create_session(
                request,
                persistence=persistence,
                checkpoint=changed,
            )
        assert rejected.value.reason is expected
    finally:
        database.close()


def test_missing_checkpoint_candle_and_modified_journal_fail_closed(tmp_path: Path) -> None:
    request = recoverable_request()
    path = tmp_path / "strategy_data.db"
    database = StrategyDataDatabase(path)
    persistence = persistence_for(database)
    state = StrategyStateV1((request.candles[0].candle_id,), 1)
    missing = WarmupCheckpoint.create(
        request.context,
        manifest_sha256=request.manifest_hash,
        config_sha256=request.configuration_hash,
        runtime_phase=RuntimePhase.WARMING_UP,
        state=state,
        last_close_time_ms=request.candles[0].close_time_ms,
        created_at_ms=request.candles[0].close_time_ms,
    )
    with pytest.raises(CheckpointRestoreError) as rejected:
        ReplayEngine(catalog_factory).create_session(
            request,
            persistence=persistence,
            checkpoint=missing,
        )
    assert rejected.value.reason is CheckpointRestoreReason.CANDLE_MISSING

    session = ReplayEngine(catalog_factory).create_session(request, persistence=persistence)
    session.process_many(request.candles[:3])
    session.checkpoint()
    database.close()
    raw = sqlite3.connect(path)
    try:
        raw.execute(
            "UPDATE decision_events SET payload_json = ? WHERE run_id = ? AND sequence = 1",
            ('[["source","TAMPERED"]]', request.run_id),
        )
        raw.commit()
    finally:
        raw.close()
    reopened = StrategyDataDatabase(path)
    try:
        with pytest.raises(JournalConflictError):
            ReplayEngine(catalog_factory).create_session(
                request,
                persistence=persistence_for(reopened),
            )
    finally:
        reopened.close()
