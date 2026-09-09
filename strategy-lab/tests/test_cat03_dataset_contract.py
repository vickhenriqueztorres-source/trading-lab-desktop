from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from primitives import Candle
from strategy_lab.cli import main
from strategy_lab.research.candidate import Candidate
from strategy_lab.research.dataset import DatasetOrigin, ResearchDataset, ResearchDatasetError
from strategy_lab.research.evidence import FakeEvidenceRepository
from strategy_lab.research.payout_lookup import PayoutLookup
from strategy_lab.research.runner import run_research_pipeline
from strategy_lab.research.synthetic import edge_series

START_TS = 1_700_000_100


def test_assets_are_exact_and_otc_never_merges_with_spot() -> None:
    dataset = ResearchDataset.from_rows(
        _rows("EURUSD-OTC", 10),
        [],
        [],
        _sessions("EURUSD-OTC"),
        origin=DatasetOrigin.PARQUET,
        source="test:exact-asset",
    )

    assert dataset.coverage("EURUSD", START_TS, START_TS + 9 * 60) == Decimal("0")
    bundle = dataset.bundle_for(
        "EURUSD-OTC",
        60,
        START_TS,
        START_TS + 9 * 60,
        now_ts=START_TS + 20 * 60,
    )

    assert len(bundle.candles) == 10
    assert bundle.snapshot.identity.asset == "EURUSD-OTC"


def test_timeframe_buckets_are_complete_utc_buckets() -> None:
    dataset = ResearchDataset.from_rows(
        _rows("EURUSD", 15),
        [],
        [],
        [],
        origin=DatasetOrigin.PARQUET,
        source="test:timeframes",
    )

    assert len(dataset.candles_for("EURUSD", START_TS, START_TS + 14 * 60, timeframe_s=60)) == 15
    assert len(dataset.candles_for("EURUSD", START_TS, START_TS + 14 * 60, timeframe_s=300)) == 3
    assert len(dataset.candles_for("EURUSD", START_TS, START_TS + 14 * 60, timeframe_s=900)) == 1
    assert len(dataset.candles_for("EURUSD", START_TS, START_TS + 13 * 60, timeframe_s=900)) == 0


def test_current_candle_is_rejected_by_bundle_contract() -> None:
    dataset = ResearchDataset.from_rows(
        _rows("EURUSD", 10),
        [],
        [],
        [],
        origin=DatasetOrigin.PARQUET,
        source="test:current-candle",
    )

    try:
        dataset.bundle_for(
            "EURUSD",
            60,
            START_TS,
            START_TS + 9 * 60,
            now_ts=START_TS + 10 * 60,
        )
    except ResearchDatasetError as exc:
        assert str(exc) == "RES_CURRENT_CANDLE_FORBIDDEN"
    else:
        raise AssertionError("current candle must be rejected")


def test_payout_lookup_is_exact_asset_and_asof_only() -> None:
    hour_ts = START_TS - START_TS % 3600
    lookup = PayoutLookup.from_rows(
        [
            {
                "asset": "EURUSD",
                "hour_ts": hour_ts,
                "observed_at": START_TS,
                "payout_pct": "86.00",
                "samples": 1,
            },
            {
                "asset": "EURUSD-OTC",
                "hour_ts": hour_ts,
                "observed_at": START_TS + 60,
                "payout_pct": "87.00",
                "samples": 1,
            },
            {
                "asset": "GBPUSD",
                "hour_ts": hour_ts,
                "observed_at": START_TS,
                "payout_pct": "88.00",
                "samples": 0,
            },
            {"asset": "USDJPY", "hour_ts": hour_ts, "payout_pct": "89.00", "samples": 1},
        ]
    )

    assert lookup.payout("EURUSD-OTC", START_TS) is None
    assert lookup.decision("EURUSD-OTC", START_TS).reason == "RES_PAYOUT_OBSERVED_AFTER_SIGNAL"
    assert lookup.payout("EURUSD", START_TS) == Decimal("0.86")
    assert lookup.decision("GBPUSD", START_TS).reason == "RES_PAYOUT_SAMPLES_ZERO"
    assert lookup.decision("USDJPY", START_TS).reason == "RES_PAYOUT_LEGACY_NO_ASOF"


def test_missing_tick_volume_makes_volume_family_ineligible(tmp_path: Path) -> None:
    candles = [
        Candle(ts=item.ts, o=item.o, h=item.h, l=item.l, c=item.c, tick_vol=None)
        for item in edge_series(seed=3, length=120)
    ]
    snapshot = (
        ResearchDataset.from_rows(
            [
                {
                    "asset": "EURUSD-OTC",
                    "ts": candle.ts,
                    "o": candle.o,
                    "h": candle.h,
                    "l": candle.l,
                    "c": candle.c,
                    "tick_vol": None,
                }
                for candle in candles
            ],
            [],
            [],
            _sessions("EURUSD-OTC"),
            origin=DatasetOrigin.PARQUET,
            source="test:no-volume",
        )
        .bundle_for(
            "EURUSD-OTC",
            60,
            candles[0].ts,
            candles[-1].ts,
            now_ts=candles[-1].ts + 600,
            require_approval_quality=False,
        )
        .snapshot
    )
    result = run_research_pipeline(
        candles,
        PayoutLookup([]),
        run_id="test_no_volume",
        assets=["EURUSD-OTC"],
        output_dir=tmp_path,
        dataset_snapshot=snapshot,
        override_candidates=[
            Candidate(
                family="F4",
                regime="bb_width_ratio",
                trigger="range_break",
                confirm="tick_volume_ratio",
                asset="EURUSD-OTC",
            )
        ],
        min_oos_trades=1,
        enforce_holdout_pass=False,
        evidence_repository=FakeEvidenceRepository(),
    )

    assert result.reports[0].approval.reason == "RES_TICK_VOLUME_UNAVAILABLE"


def test_out_of_session_gaps_do_not_block_coverage() -> None:
    dataset = ResearchDataset.from_rows(
        _rows("EURUSD", 5),
        [],
        [
            {
                "asset": "EURUSD",
                "from_ts": START_TS,
                "to_ts": START_TS + 60,
                "in_session": False,
                "resolved": False,
            }
        ],
        [],
        origin=DatasetOrigin.PARQUET,
        source="test:session-gaps",
    )

    dataset.refuse_if_coverage_below("EURUSD", START_TS, START_TS + 4 * 60)


def test_snapshot_fingerprint_is_immutable_and_source_bound() -> None:
    dataset_a = ResearchDataset.from_rows(
        _rows("EURUSD", 20), [], [], [], origin=DatasetOrigin.PARQUET, source="test:a"
    )
    snapshots = [
        dataset_a.bundle_for(
            "EURUSD",
            60,
            START_TS,
            START_TS + 19 * 60,
            now_ts=START_TS + 40 * 60,
        ).snapshot
        for _ in range(3)
    ]
    dataset_b = ResearchDataset.from_rows(
        _rows("EURUSD", 20), [], [], [], origin=DatasetOrigin.PARQUET, source="test:b"
    )
    snapshot_b = dataset_b.bundle_for(
        "EURUSD", 60, START_TS, START_TS + 19 * 60, now_ts=START_TS + 40 * 60
    ).snapshot

    assert len({snapshot.fingerprint for snapshot in snapshots}) == 1
    assert snapshots[0].fingerprint != snapshot_b.fingerprint


def test_research_cli_requires_explicit_real_or_synthetic_source(tmp_path: Path) -> None:
    assert main(["research", "--seed", "1", "--output-dir", str(tmp_path)]) == 1


def _rows(asset: str, count: int) -> list[dict[str, object]]:
    return [
        {
            "asset": asset,
            "ts": START_TS + index * 60,
            "o": "1.00",
            "h": "1.03",
            "l": "0.99",
            "c": "1.01",
            "tick_vol": 100 + index,
        }
        for index in range(count)
    ]


def _sessions(asset: str) -> list[dict[str, object]]:
    weekday = (START_TS // 86400 + 3) % 7
    return [{"asset": asset, "weekday": weekday, "open_min": 0, "close_min": 1440}]
