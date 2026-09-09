"""CAT-17 contract tests for R-HUB-7, R-OPS-1 and R-RES-1."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path

import pytest
from strategy_lab.archive import (
    ArchiveError,
    ArchiveExecutor,
    ArchiveJob,
    ArchivePayoutObservation,
    ArchiveProof,
    ArchiveRow,
    ArchiveStatus,
    MemoryObjectStore,
    VerifiedColdArchiveReader,
    backup_verified_objects,
    content_sha256,
    decode_and_verify_parquet,
    encode_parquet,
    restore_verified_objects,
)
from strategy_lab.collect.repository import FakeRepository, source_for_asset
from strategy_lab.research.dataset import (
    ResearchDataset,
    _merge_hot_and_cold_candles,
    _merge_hot_and_cold_payouts,
)
from strategy_lab.research.payout_lookup import PayoutLookup

ROOT = Path(__file__).resolve().parents[1]


def archive_rows() -> list[ArchiveRow]:
    return [
        ArchiveRow(
            asset="EURUSD-OTC",
            ts=1_700_000_040 + offset * 60,
            o=Decimal("1.10000000") + Decimal(offset) / Decimal("100000"),
            h=Decimal("1.20000000"),
            l=Decimal("1.00000000"),
            c=Decimal("1.11000000") + Decimal(offset) / Decimal("100000"),
            tick_vol=10 + offset,
            source="EURUSD-OTC|fixture@abc",
            collected_at=1_800_000_000,
            payout_observations=(
                ArchivePayoutObservation(
                    observed_at=1_700_000_040 + offset * 60 + 5,
                    payout_pct=Decimal("87.00"),
                    source="fixture",
                ),
            ),
        )
        for offset in range(3)
    ]


@dataclass
class MemoryArchiveRepository:
    rows: list[ArchiveRow]
    job: ArchiveJob = field(init=False)
    hot: dict[tuple[str, int], ArchiveRow] = field(init=False)
    proof: ArchiveProof | None = None
    failed_reason: str | None = None
    deleted: int = 0

    def __post_init__(self) -> None:
        self.job = ArchiveJob(
            "00000000-0000-4000-8000-000000000017",
            self.rows[0].asset,
            self.rows[0].ts,
            self.rows[-1].ts + 60,
            len(self.rows),
            "00000000-0000-4000-8000-000000000018",
        )
        self.hot = {(row.asset, row.ts): row for row in self.rows}

    def claim(self, worker_id: str, lease_seconds: int) -> ArchiveJob | None:
        assert worker_id
        assert lease_seconds >= 60
        if self.job.status in {ArchiveStatus.COMPLETED, ArchiveStatus.FAILED}:
            return None
        return self.job

    def frozen_rows(self, job: ArchiveJob) -> list[ArchiveRow]:
        assert job.job_id == self.job.job_id
        return list(self.rows)

    def mark_verified(self, job: ArchiveJob, proof: ArchiveProof) -> None:
        assert job.claim_token == self.job.claim_token
        self.proof = proof
        self.job = replace(self.job, status=ArchiveStatus.VERIFIED)

    def complete_exact(self, job: ArchiveJob) -> int:
        assert self.proof is not None
        if any(self.hot.get((row.asset, row.ts)) != row for row in self.rows):
            return 0
        for row in self.rows:
            del self.hot[(row.asset, row.ts)]
        self.deleted = len(self.rows)
        self.job = replace(self.job, status=ArchiveStatus.COMPLETED)
        return self.deleted

    def mark_failed(self, job: ArchiveJob, reason: str) -> None:
        self.failed_reason = reason
        self.job = replace(self.job, status=ArchiveStatus.FAILED)

    def verified_objects(
        self, assets: list[str], from_ts: int, to_ts: int
    ) -> list[dict[str, object]]:
        if self.proof is None or self.job.status is not ArchiveStatus.COMPLETED:
            return []
        return [
            {
                "asset": self.job.asset,
                "from_ts": self.job.from_ts,
                "to_ts": self.job.to_ts,
                "object_path": self.proof.object_path,
                "object_sha256": self.proof.object_sha256,
                "content_sha256": self.proof.content_sha256,
                "row_count": self.proof.row_count,
                "object_bytes": self.proof.object_bytes,
            }
        ]


def test_decimal_parquet_round_trip_preserves_ohlc_payout_and_fingerprint() -> None:
    """R-HUB-7: Parquet round-trip preserves exact logical content."""
    rows = archive_rows()
    payload, ratio = encode_parquet(rows)
    decoded = decode_and_verify_parquet(payload, rows)

    assert decoded == rows
    assert content_sha256(decoded) == content_sha256(rows)
    assert ratio > Decimal("0")
    assert all(isinstance(row.o, Decimal) for row in decoded)
    assert decoded[0].payout_observations[0].payout_pct == Decimal("87.00")


def test_executor_uploads_downloads_verifies_then_deletes_exact_rows() -> None:
    """R-HUB-7: removal happens only after a downloaded object has complete proof."""
    repository = MemoryArchiveRepository(archive_rows())
    storage = MemoryObjectStore({})

    report = ArchiveExecutor(repository, storage, worker_id="test").run_once()

    assert report.status == "completed"
    assert report.deleted_count == 3
    assert repository.hot == {}
    assert repository.proof is not None
    assert hashlib.sha256(storage.get(report.object_path or "")).hexdigest() == report.object_sha256


class CorruptingStore(MemoryObjectStore):
    def get(self, path: str) -> bytes:
        return super().get(path)[:-8] + b"corrupt!"


class QuotaStore(MemoryObjectStore):
    def put_immutable(self, path: str, payload: bytes) -> None:
        raise ArchiveError("ARCHIVE_STORAGE_QUOTA")


class CrashOnceAfterUploadStore(MemoryObjectStore):
    crashed: bool = False

    def get(self, path: str) -> bytes:
        if not self.crashed:
            self.crashed = True
            raise SystemExit("simulated process crash")
        return super().get(path)


@pytest.mark.parametrize(
    ("store", "reason"),
    [
        (CorruptingStore({}), "ARCHIVE_OBJECT_HASH_MISMATCH"),
        (QuotaStore({}), "ARCHIVE_STORAGE_QUOTA"),
    ],
)
def test_storage_failure_never_removes_hot_rows(store: MemoryObjectStore, reason: str) -> None:
    """R-HUB-7: corrupt downloads and quota errors fail closed before deletion."""
    repository = MemoryArchiveRepository(archive_rows())

    report = ArchiveExecutor(repository, store, worker_id="test").run_once()

    assert report.status == "failed"
    assert report.reason == reason
    assert len(repository.hot) == 3
    assert repository.deleted == 0


def test_late_update_aborts_exact_delete_even_when_count_is_unchanged() -> None:
    """R-HUB-7: a same-count late correction cannot be lost by range deletion."""
    repository = MemoryArchiveRepository(archive_rows())
    original = repository.rows[1]
    repository.hot[(original.asset, original.ts)] = replace(original, c=Decimal("1.15000000"))

    report = ArchiveExecutor(repository, MemoryObjectStore({}), worker_id="test").run_once()

    assert report.status == "failed"
    assert report.reason == "ARCHIVE_EXACT_DELETE_ABORTED"
    assert len(repository.hot) == 3


def test_immutable_retry_is_idempotent_and_different_content_conflicts() -> None:
    """R-HUB-7: retrying identical upload is safe; path reuse with other bytes is rejected."""
    rows = archive_rows()
    payload, _ratio = encode_parquet(rows)
    storage = MemoryObjectStore({})
    path = "parquet/EURUSD-OTC/1700000040-1700000220-deadbeefdeadbeef.parquet"

    storage.put_immutable(path, payload)
    storage.put_immutable(path, payload)
    with pytest.raises(ArchiveError, match="ARCHIVE_IMMUTABLE_OBJECT_CONFLICT"):
        storage.put_immutable(path, payload + b"different")


def test_crash_after_upload_recovers_idempotently_without_duplicate_object() -> None:
    """R-HUB-7: a process crash leaves hot rows and the immutable upload reusable."""
    repository = MemoryArchiveRepository(archive_rows())
    storage = CrashOnceAfterUploadStore({})
    executor = ArchiveExecutor(repository, storage, worker_id="test")

    with pytest.raises(SystemExit, match="simulated process crash"):
        executor.run_once()
    assert len(repository.hot) == 3
    assert len(storage.objects) == 1

    report = executor.run_once()

    assert report.status == "completed"
    assert report.deleted_count == 3
    assert len(storage.objects) == 1


def test_executor_off_leaves_backlog_and_hot_rows_untouched() -> None:
    """R-HUB-7: absence of the local executor cannot mark or remove a planned job."""
    repository = MemoryArchiveRepository(archive_rows())

    assert repository.job.status is ArchiveStatus.CLAIMED
    assert repository.proof is None
    assert len(repository.hot) == 3


def test_verified_cold_reader_rejects_object_changed_after_indexing() -> None:
    """R-OPS-1: cold reads re-check the durable object proof."""
    repository = MemoryArchiveRepository(archive_rows())
    storage = MemoryObjectStore({})
    report = ArchiveExecutor(repository, storage, worker_id="test").run_once()
    assert report.object_path is not None
    storage.objects[report.object_path] += b"changed"

    reader = VerifiedColdArchiveReader(repository, storage)
    with pytest.raises(ArchiveError, match="ARCHIVE_OBJECT_SIZE_MISMATCH"):
        reader.read(["EURUSD-OTC"], repository.job.from_ts, repository.job.to_ts)


def test_cold_object_backup_restore_verifies_isolated_destination(tmp_path: Path) -> None:
    """R-OPS-1: Storage objects are backed up and verified after isolated restore."""
    repository = MemoryArchiveRepository(archive_rows())
    source = MemoryObjectStore({})
    report = ArchiveExecutor(repository, source, worker_id="test").run_once()
    assert report.status == "completed"
    records = repository.verified_objects(
        [repository.job.asset], repository.job.from_ts, repository.job.to_ts
    )
    manifest = backup_verified_objects(records, source, tmp_path)
    destination = MemoryObjectStore({})

    restored = restore_verified_objects(manifest, destination)

    assert restored == 1
    assert destination.objects == source.objects


def test_hot_cold_research_merge_has_no_duplicate_and_same_replay_inputs() -> None:
    """R-RES-1: hot+cold reconstruction yields the same candles and point-in-time payout."""
    rows = archive_rows()
    hot = [
        {
            "asset": row.asset,
            "ts": row.ts,
            "o": row.o,
            "h": row.h,
            "l": row.l,
            "c": row.c,
            "tick_vol": row.tick_vol,
            "source": row.source,
            "collected_at": row.collected_at,
        }
        for row in rows
    ]
    payouts = [
        {
            "asset": row.asset,
            "observed_at": observation.observed_at,
            "hour_ts": observation.observed_at - observation.observed_at % 3600,
            "payout_pct": observation.payout_pct,
            "samples": 1,
            "source": observation.source,
        }
        for row in rows
        for observation in row.payout_observations
    ]
    sessions = [
        {"asset": "EURUSD-OTC", "weekday": weekday, "open_min": 0, "close_min": 1440}
        for weekday in range(7)
    ]
    before = ResearchDataset.from_rows(hot, payouts, sessions=sessions)
    merged_candles = _merge_hot_and_cold_candles(hot[2:], rows[:2])
    merged_payouts = _merge_hot_and_cold_payouts(payouts[2:], rows[:2])
    after = ResearchDataset.from_rows(merged_candles, merged_payouts, sessions=sessions)

    assert after.candles_for("EURUSD-OTC", rows[0].ts, rows[-1].ts) == before.candles_for(
        "EURUSD-OTC", rows[0].ts, rows[-1].ts
    )
    before_payout = PayoutLookup.from_rows(before.payouts.to_dicts())
    after_payout = PayoutLookup.from_rows(after.payouts.to_dicts())
    for row in rows:
        assert after_payout.decision(row.asset, row.ts + 5) == before_payout.decision(
            row.asset, row.ts + 5
        )


def test_hot_cold_same_pk_different_content_fails_closed() -> None:
    """R-RES-1: equal primary keys and counts cannot conceal divergent archive content."""
    row = archive_rows()[0]
    hot = [
        {
            "asset": row.asset,
            "ts": row.ts,
            "o": row.o,
            "h": row.h,
            "l": row.l,
            "c": Decimal("1.15000000"),
            "tick_vol": row.tick_vol,
            "source": row.source,
            "collected_at": row.collected_at,
        }
    ]
    with pytest.raises(ArchiveError):
        encode_parquet([row, row])
    with pytest.raises(RuntimeError, match="RES_HOT_COLD_CONTENT_CONFLICT"):
        _merge_hot_and_cold_candles(hot, [row])


def test_collect_watermark_survives_removal_from_hot_layer() -> None:
    """R-COL-3: archived hot candles do not reset the durable collection watermark."""
    repository = FakeRepository()
    source = source_for_asset("EURUSD-OTC", "abc")
    from primitives import Candle

    repository.upsert_candles(
        [
            Candle(
                ts=1_700_000_040,
                o=Decimal("1.1"),
                h=Decimal("1.2"),
                l=Decimal("1.0"),
                c=Decimal("1.1"),
                tick_vol=1,
            )
        ],
        source,
    )
    repository.candles.clear()

    assert repository.watermark("EURUSD-OTC") == 1_700_000_040


def test_corrective_migration_disables_unsafe_completion_and_exact_deletes() -> None:
    """R-HUB-7: SQL disables count-only completion and binds DELETE to frozen PK/content."""
    sql = (ROOT / "apps/hub/supabase/migrations/0012_verified_cold_archive.sql").read_text(
        encoding="utf-8"
    )

    assert "ARCHIVE_UNSAFE_COMPLETION_DISABLED" in sql
    assert "complete_verified_cold_archive_job" in sql
    assert "using public.cold_archive_job_rows" in sql
    assert "ARCHIVE_LATE_UPDATE_DETECTED" in sql
    assert "grant execute" in sql
    assert "from public, anon, authenticated" in sql
