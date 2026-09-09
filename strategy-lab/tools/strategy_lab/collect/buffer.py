"""Bounded, validated collection buffer; no backend mutation before commit (R-COL-5/9)."""

from __future__ import annotations

from decimal import Decimal

from primitives import Candle

from strategy_lab.collect.invariants import check_invariants
from strategy_lab.collect.repository import FakeRepository, GapRecord, Repository, RepositoryError

# At most 100k price/gap/payout records per job, approximately four days of 16 M1 assets.
# Exhaustion aborts with zero writes; larger experiments need a reviewed collection budget.
MAX_BUFFERED_RECORDS = 100_000


class CollectionBuffer(FakeRepository):
    def __init__(self, source: Repository) -> None:
        super().__init__()
        self.source = source

    def watermark(self, asset: str) -> int | None:
        values = [
            value
            for value in (super().watermark(asset), self.source.watermark(asset))
            if value is not None
        ]
        return max(values) if values else None

    def _reserve(self, extra: int) -> None:
        count = len(self.candles) + len(self.gaps) + len(self.payout_observations)
        if count + extra > MAX_BUFFERED_RECORDS:
            raise RepositoryError("COL_BUFFER_BUDGET_EXHAUSTED")

    def upsert_candles(self, candles: list[Candle], source: str) -> int:
        self._reserve(len(candles))
        return super().upsert_candles(candles, source)

    def record_gaps(self, asset: str, gaps: list[GapRecord]) -> None:
        self._reserve(len(gaps))
        super().record_gaps(asset, gaps)

    def upsert_payout(
        self, asset: str, hour_ts: int, value: Decimal, *, observed_at: int, source: str
    ) -> None:
        self._reserve(1)
        if not isinstance(value, Decimal) or not value.is_finite() or not 0 < value <= 1:
            raise RepositoryError("COL_PAYOUT_VALUE_INVALID")
        super().upsert_payout(asset, hour_ts, value, observed_at=observed_at, source=source)

    def validate(self) -> None:
        assets = {asset for asset, _ts in self.candles}
        for asset in assets:
            rows = sorted(
                (c for (a, _), (c, _) in self.candles.items() if a == asset), key=lambda c: c.ts
            )
            if check_invariants(rows):
                raise RepositoryError("COL_COLLECTION_INVARIANT_FAILED")

    def persist(self, report: dict[str, object], asset_reports: list[dict[str, object]]) -> None:
        """One transaction contains all candles, gaps, payout observations and run report."""
        self.validate()
        with self.source.transaction():
            for asset_report in asset_reports:
                asset = str(asset_report["asset"])
                sources = {s for (a, _), (_, s) in self.candles.items() if a == asset}
                inserted = 0
                for source in sorted(sources):
                    rows = [
                        c for (a, _), (c, s) in self.candles.items() if a == asset and s == source
                    ]
                    for offset in range(0, len(rows), 1000):
                        inserted += self.source.upsert_candles(rows[offset : offset + 1000], source)
                if "written" in asset_report:
                    asset_report["written"] = inserted
                self.source.record_gaps(asset, [g for g in self.gaps if g.asset == asset])
            for observation in self.payout_observations.values():
                self.source.upsert_payout(
                    observation.asset,
                    observation.observed_at // 3600 * 3600,
                    observation.payout_pct / Decimal(100),
                    observed_at=observation.observed_at,
                    source=observation.source,
                )
            self.source.record_run(report)
