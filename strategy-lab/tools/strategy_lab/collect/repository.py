"""Repository protocol and in-memory implementation for collect (R-COL-6, R-COL-10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from primitives import Candle


@dataclass(frozen=True)
class GapRecord:
    asset: str
    from_ts: int
    to_ts: int
    detected_at: int
    in_session: bool
    resolved: bool = False


@dataclass
class PayoutRecord:
    asset: str
    hour_ts: int
    payout_pct: Decimal | None = None
    samples: int = 0


class Repository(Protocol):
    def watermark(self, asset: str) -> int | None: ...
    def upsert_candles(self, candles: list[Candle], source: str) -> int: ...
    def record_gaps(self, asset: str, gaps: list[GapRecord]) -> None: ...
    def upsert_payout(self, asset: str, hour_ts: int, value: Decimal) -> None: ...
    def record_run(self, report: dict[str, object]) -> None: ...


@dataclass
class FakeRepository:
    candles: dict[tuple[str, int], tuple[Candle, str]] = field(default_factory=dict)
    gaps: list[GapRecord] = field(default_factory=list)
    payouts: dict[tuple[str, int], PayoutRecord] = field(default_factory=dict)
    runs: list[dict[str, object]] = field(default_factory=list)
    last_status: str = "never_run"

    def watermark(self, asset: str) -> int | None:
        values = [ts for (stored_asset, ts) in self.candles if stored_asset == asset]
        return max(values) if values else None

    def upsert_candles(self, candles: list[Candle], source: str) -> int:
        inserted = 0
        for candle in candles:
            key = (_asset_from_source(source), candle.ts)
            existing = self.candles.get(key)
            if existing is not None and existing[1] != source:
                raise RepositoryError("COL_SOURCE_CONFLICT")
            if existing is None:
                inserted += 1
            self.candles[key] = (candle, source)
        return inserted

    def record_gaps(self, asset: str, gaps: list[GapRecord]) -> None:
        existing = {(item.asset, item.from_ts, item.to_ts) for item in self.gaps}
        for gap in gaps:
            key = (gap.asset, gap.from_ts, gap.to_ts)
            if key not in existing:
                self.gaps.append(gap)
                existing.add(key)

    def upsert_payout(self, asset: str, hour_ts: int, value: Decimal) -> None:
        key = (asset, hour_ts)
        record = self.payouts.get(key)
        if record is None:
            self.payouts[key] = PayoutRecord(
                asset=asset, hour_ts=hour_ts, payout_pct=value, samples=1
            )
            return
        total = (record.payout_pct or Decimal("0")) * record.samples + value
        record.samples += 1
        record.payout_pct = total / Decimal(record.samples)

    def record_run(self, report: dict[str, object]) -> None:
        self.runs.append(dict(report))
        self.last_status = str(report.get("status", "unknown"))


class RepositoryError(RuntimeError):
    pass


def source_for_asset(asset: str, upstream_commit: str) -> str:
    return f"{asset}|iqoptionapi@{upstream_commit}"


def _asset_from_source(source: str) -> str:
    asset, separator, _tail = source.partition("|")
    if not separator or not asset:
        raise RepositoryError("COL_SOURCE_INVALID")
    return asset
