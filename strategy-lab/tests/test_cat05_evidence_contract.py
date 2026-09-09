"""CAT-05: durable evidence and persistent holdout fail-closed behavior."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

import pytest
from primitives import Candle
from strategy_lab.research.dataset import (
    DATASET_SCHEMA_VERSION,
    DatasetIdentity,
    DatasetOrigin,
    DatasetQuality,
    DatasetSnapshot,
    synthetic_snapshot,
)
from strategy_lab.research.evidence import (
    DatasetSnapshotRecord,
    EvidenceUnavailable,
    FakeEvidenceRepository,
    HoldoutReservation,
    MemoryArtifactStore,
)
from strategy_lab.research.holdout import HoldoutManager
from strategy_lab.research.payout_lookup import PayoutLookup, PayoutPoint
from strategy_lab.research.runner import run_research_pipeline
from strategy_lab.research.synthetic import (
    BASE_TS,
    edge_series,
    make_injected_edge_candidate,
    random_walk,
    register_synthetic_primitives,
)


def _payout_lookup(payout: str = "0.87") -> PayoutLookup:
    return PayoutLookup(
        [
            PayoutPoint(
                "EURUSD-OTC",
                BASE_TS - BASE_TS % 3600 + offset * 3600,
                Decimal(payout),
                1,
            )
            for offset in range(50)
        ]
    )


def _production_snapshot(candles: list[Candle]) -> DatasetSnapshot:
    quality = DatasetQuality(
        present=len(candles),
        expected=len(candles),
        coverage=Decimal("1"),
        unresolved_in_session_gaps=0,
        tick_volume_available=True,
        approval_eligible=True,
    )
    return DatasetSnapshot(
        identity=DatasetIdentity(
            schema_version=DATASET_SCHEMA_VERSION,
            origin=DatasetOrigin.SUPABASE,
            source="supabase:staging:test",
            asset="EURUSD-OTC",
            timeframe_s=60,
            from_ts=candles[0].ts,
            to_ts=candles[-1].ts,
        ),
        quality=quality,
        fingerprint="sha256:" + "1" * 64,
    )


def test_production_eligible_research_requires_durable_evidence(tmp_path) -> None:
    register_synthetic_primitives()
    candles = edge_series(seed=1, length=2000, win_probability_pct=65)
    with pytest.raises(EvidenceUnavailable, match="RES_DURABLE_EVIDENCE_REQUIRED"):
        run_research_pipeline(
            candles,
            _payout_lookup(),
            run_id="cat05_missing_evidence",
            assets=["EURUSD-OTC"],
            output_dir=tmp_path,
            dataset_snapshot=_production_snapshot(candles),
            override_candidates=[make_injected_edge_candidate("EURUSD-OTC")],
            min_oos_trades=50,
        )


def test_pipeline_records_dataset_attempt_artifact_and_burns_holdout(tmp_path) -> None:
    register_synthetic_primitives()
    candles = edge_series(seed=1, length=2000, win_probability_pct=65)
    snapshot = synthetic_snapshot(candles, asset="EURUSD-OTC", seed=1)
    repository = FakeEvidenceRepository()
    artifact_store = MemoryArtifactStore(run_id="cat05_durable")

    result = run_research_pipeline(
        candles,
        _payout_lookup(),
        run_id="cat05_durable",
        assets=["EURUSD-OTC"],
        output_dir=tmp_path,
        dataset_snapshot=snapshot,
        override_candidates=[make_injected_edge_candidate("EURUSD-OTC")],
        min_oos_trades=50,
        enforce_holdout_pass=False,
        evidence_repository=repository,
        artifact_store=artifact_store,
        require_durable_evidence=True,
    )

    assert result.approved_count == 1
    assert snapshot.fingerprint in repository.dataset_snapshots
    assert len(repository.attempts) == 1
    assert len(repository.artifacts) == 1
    assert all(state == "burned" for _reservation, state in repository.holdouts.values())
    assert all(path.startswith("private/evidence/sha256/") for path in artifact_store.objects)


def test_holdout_burned_in_previous_run_blocks_next_run(tmp_path) -> None:
    register_synthetic_primitives()
    candles = edge_series(seed=1, length=2000, win_probability_pct=65)
    snapshot = synthetic_snapshot(candles, asset="EURUSD-OTC", seed=1)
    repository = FakeEvidenceRepository()
    candidate = make_injected_edge_candidate("EURUSD-OTC")

    run_research_pipeline(
        candles,
        _payout_lookup(),
        run_id="cat05_first",
        assets=["EURUSD-OTC"],
        output_dir=tmp_path / "first",
        dataset_snapshot=snapshot,
        override_candidates=[candidate],
        min_oos_trades=50,
        enforce_holdout_pass=False,
        evidence_repository=repository,
        require_durable_evidence=True,
    )

    with pytest.raises(EvidenceUnavailable, match="RES_HOLDOUT_RANGE_BURNED"):
        run_research_pipeline(
            candles,
            _payout_lookup(),
            run_id="cat05_second",
            assets=["EURUSD-OTC"],
            output_dir=tmp_path / "second",
            dataset_snapshot=snapshot,
            override_candidates=[candidate],
            min_oos_trades=50,
            enforce_holdout_pass=False,
            evidence_repository=repository,
            require_durable_evidence=True,
        )


def test_run_without_preapproved_candidates_rolls_back_holdout(tmp_path) -> None:
    register_synthetic_primitives()
    candles = random_walk(seed=42, length=2000)
    repository = FakeEvidenceRepository()

    result = run_research_pipeline(
        candles,
        _payout_lookup(),
        run_id="cat05_no_preapproved",
        assets=["EURUSD-OTC"],
        seed=42,
        output_dir=tmp_path,
        dataset_snapshot=synthetic_snapshot(candles, asset="EURUSD-OTC", seed=42),
        override_candidates=[make_injected_edge_candidate("EURUSD-OTC")],
        min_oos_trades=50,
        evidence_repository=repository,
        require_durable_evidence=True,
    )

    assert result.approved_count == 0
    assert all(state == "rolled_back" for _reservation, state in repository.holdouts.values())


def test_two_concurrent_runs_cannot_reserve_same_holdout() -> None:
    candles = random_walk(seed=2, length=100)
    snapshot = synthetic_snapshot(candles, asset="EURUSD-OTC", seed=2)
    repository = FakeEvidenceRepository()
    repository.record_dataset_snapshot(DatasetSnapshotRecord.from_snapshot(snapshot))
    reservation_a = HoldoutReservation.create(
        run_id="cat05_a",
        snapshot=snapshot,
        holdout_range=(candles[-20].ts, candles[-1].ts),
        holdout_hash="2" * 64,
    )
    reservation_b = HoldoutReservation.create(
        run_id="cat05_b",
        snapshot=snapshot,
        holdout_range=(candles[-20].ts, candles[-1].ts),
        holdout_hash="2" * 64,
    )

    def reserve(reservation: HoldoutReservation) -> str:
        try:
            repository.reserve_holdout(reservation)
        except EvidenceUnavailable as exc:
            return str(exc)
        return "reserved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(reserve, (reservation_a, reservation_b)))

    assert results == ["RES_HOLDOUT_ALREADY_RESERVED", "reserved"]


def test_evidence_read_and_write_failures_abort(tmp_path) -> None:
    register_synthetic_primitives()
    candles = random_walk(seed=3, length=2000)
    snapshot = synthetic_snapshot(candles, asset="EURUSD-OTC", seed=3)

    with pytest.raises(EvidenceUnavailable, match="RES_EVIDENCE_WRITE_FAILED"):
        run_research_pipeline(
            candles,
            _payout_lookup(),
            run_id="cat05_write_fail",
            assets=["EURUSD-OTC"],
            output_dir=tmp_path / "write",
            dataset_snapshot=snapshot,
            override_candidates=[make_injected_edge_candidate("EURUSD-OTC")],
            min_oos_trades=50,
            evidence_repository=FakeEvidenceRepository(fail_writes=True),
            require_durable_evidence=True,
        )

    with pytest.raises(EvidenceUnavailable, match="RES_EVIDENCE_READ_FAILED"):
        run_research_pipeline(
            candles,
            _payout_lookup(),
            run_id="cat05_read_fail",
            assets=["EURUSD-OTC"],
            output_dir=tmp_path / "read",
            dataset_snapshot=snapshot,
            override_candidates=[make_injected_edge_candidate("EURUSD-OTC")],
            min_oos_trades=50,
            evidence_repository=FakeEvidenceRepository(fail_reads=True),
            require_durable_evidence=True,
        )


def test_legacy_holdout_manager_database_errors_are_not_swallowed() -> None:
    class Cursor:
        def execute(self, *_args: Any) -> None:
            raise RuntimeError("db down")

        def fetchall(self) -> list[tuple[int, int]]:
            return []

    class BrokenConnection:
        def cursor(self) -> Cursor:
            return Cursor()

    with pytest.raises(RuntimeError, match="db down"):
        HoldoutManager(db_connection=BrokenConnection())
