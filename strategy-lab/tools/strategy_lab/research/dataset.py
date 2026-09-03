"""Research dataset loaders and coverage gates (R-RES-1)."""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from primitives import Candle

MIN_COVERAGE = Decimal("0.95")


class ResearchDatasetError(RuntimeError):
    pass


@dataclass(frozen=True)
class CoverageEntry:
    asset: str
    from_ts: int
    to_ts: int
    present: int
    expected: int
    coverage: Decimal
    unresolved_in_session_gaps: int


class ResearchDataset:
    def __init__(self, candles: Any, payouts: Any, gaps: Any | None = None) -> None:
        self.candles = candles
        self.payouts = payouts
        self.gaps = gaps if gaps is not None else _pl().DataFrame()

    @classmethod
    def from_rows(
        cls,
        candles: Iterable[Mapping[str, object]],
        payouts: Iterable[Mapping[str, object]],
        gaps: Iterable[Mapping[str, object]] = (),
    ) -> ResearchDataset:
        return cls(
            _pl().DataFrame(list(candles)),
            _pl().DataFrame(list(payouts)),
            _pl().DataFrame(list(gaps)),
        )

    @classmethod
    def from_supabase(
        cls,
        db_url: str,
        assets: list[str],
        from_ts: int,
        to_ts: int,
    ) -> ResearchDataset:
        psycopg = importlib.import_module("psycopg")
        rows_factory = importlib.import_module("psycopg.rows").dict_row
        with (
            psycopg.connect(db_url, row_factory=rows_factory) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                    select asset, ts, o, h, l, c, tick_vol
                    from public.candles
                    where asset = any(%s) and ts between %s and %s
                    order by asset, ts
                    """,
                (assets, from_ts, to_ts),
            )
            candles = list(cursor.fetchall())
            cursor.execute(
                """
                    select asset, hour_ts, payout_pct, samples
                    from public.payouts
                    where asset = any(%s)
                      and hour_ts between %s and %s
                    order by asset, hour_ts
                    """,
                (assets, from_ts - from_ts % 3600, to_ts - to_ts % 3600),
            )
            payouts = list(cursor.fetchall())
            cursor.execute(
                """
                    select asset, from_ts, to_ts, in_session, resolved
                    from public.gaps
                    where asset = any(%s)
                      and from_ts <= %s
                      and to_ts >= %s
                    """,
                (assets, to_ts, from_ts),
            )
            gaps = list(cursor.fetchall())
        return cls.from_rows(candles, payouts, gaps)

    @classmethod
    def from_parquet(
        cls, candles_path: str, payouts_path: str, gaps_path: str | None = None
    ) -> ResearchDataset:
        duckdb = importlib.import_module("duckdb")
        connection = duckdb.connect()
        candles = connection.execute("select * from read_parquet(?)", [candles_path]).pl()
        payouts = connection.execute("select * from read_parquet(?)", [payouts_path]).pl()
        gaps = (
            connection.execute("select * from read_parquet(?)", [gaps_path]).pl()
            if gaps_path is not None
            else _pl().DataFrame()
        )
        connection.close()
        return cls(candles, payouts, gaps)

    def coverage(self, asset: str, from_ts: int, to_ts: int) -> Decimal:
        return self.coverage_entry(asset, from_ts, to_ts).coverage

    def coverage_entry(self, asset: str, from_ts: int, to_ts: int) -> CoverageEntry:
        expected = len(range(from_ts, to_ts + 1, 60))
        present = len(
            set(
                self.candles.filter(
                    (_pl().col("asset") == asset)
                    & (_pl().col("ts") >= from_ts)
                    & (_pl().col("ts") <= to_ts)
                )["ts"].to_list()
            )
        )
        coverage = Decimal(present) / Decimal(expected) if expected else Decimal("0")
        return CoverageEntry(
            asset=asset,
            from_ts=from_ts,
            to_ts=to_ts,
            present=present,
            expected=expected,
            coverage=coverage,
            unresolved_in_session_gaps=self.unresolved_in_session_gaps(asset, from_ts, to_ts),
        )

    def refuse_if_coverage_below(
        self,
        asset: str,
        from_ts: int,
        to_ts: int,
        minimum: Decimal = MIN_COVERAGE,
    ) -> None:
        entry = self.coverage_entry(asset, from_ts, to_ts)
        if entry.coverage < minimum or entry.unresolved_in_session_gaps > 0:
            raise ResearchDatasetError("RES_COVERAGE_BELOW_MINIMUM")

    def unresolved_in_session_gaps(self, asset: str, from_ts: int, to_ts: int) -> int:
        if self.gaps.is_empty() or "in_session" not in self.gaps.columns:
            return 0
        return int(
            self.gaps.filter(
                (_pl().col("asset") == asset)
                & (_pl().col("in_session") == True)  # noqa: E712 - Polars expression API.
                & (_pl().col("resolved") == False)  # noqa: E712 - Polars expression API.
                & (_pl().col("from_ts") <= to_ts)
                & (_pl().col("to_ts") >= from_ts)
            ).height
        )

    def candles_for(self, asset: str, from_ts: int, to_ts: int) -> list[Candle]:
        rows = (
            self.candles.filter(
                (_pl().col("asset") == asset)
                & (_pl().col("ts") >= from_ts)
                & (_pl().col("ts") <= to_ts)
            )
            .sort("ts")
            .to_dicts()
        )
        return [
            Candle(
                ts=int(row["ts"]),
                o=Decimal(str(row["o"])),
                h=Decimal(str(row["h"])),
                l=Decimal(str(row["l"])),
                c=Decimal(str(row["c"])),
                tick_vol=int(row["tick_vol"]),
            )
            for row in rows
        ]


def coverage_report(
    dataset: ResearchDataset, assets: list[str], from_ts: int, to_ts: int
) -> list[dict[str, object]]:
    return [
        {
            "asset": entry.asset,
            "from_ts": entry.from_ts,
            "to_ts": entry.to_ts,
            "present": entry.present,
            "expected": entry.expected,
            "coverage": format(entry.coverage, "f"),
            "unresolved_in_session_gaps": entry.unresolved_in_session_gaps,
            "accepted": entry.coverage >= MIN_COVERAGE and entry.unresolved_in_session_gaps == 0,
        }
        for entry in (dataset.coverage_entry(asset, from_ts, to_ts) for asset in assets)
    ]


def _pl() -> Any:
    return importlib.import_module("polars")
