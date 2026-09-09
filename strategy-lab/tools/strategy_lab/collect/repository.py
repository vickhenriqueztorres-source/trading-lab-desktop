"""Repository protocol and in-memory implementation for collect (R-COL-6, R-COL-10)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from copy import deepcopy
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


@dataclass(frozen=True)
class PayoutObservation:
    asset: str
    observed_at: int
    payout_pct: Decimal
    source: str


class Repository(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...
    def watermark(self, asset: str) -> int | None: ...
    def upsert_candles(self, candles: list[Candle], source: str) -> int: ...
    def record_gaps(self, asset: str, gaps: list[GapRecord]) -> None: ...
    def upsert_payout(
        self,
        asset: str,
        hour_ts: int,
        value: Decimal,
        *,
        observed_at: int,
        source: str,
    ) -> None: ...
    def record_run(self, report: dict[str, object]) -> None: ...


@dataclass
class FakeRepository:
    candles: dict[tuple[str, int], tuple[Candle, str]] = field(default_factory=dict)
    durable_watermarks: dict[str, int] = field(default_factory=dict)
    gaps: list[GapRecord] = field(default_factory=list)
    payouts: dict[tuple[str, int], PayoutRecord] = field(default_factory=dict)
    payout_observations: dict[tuple[str, int], PayoutObservation] = field(default_factory=dict)
    runs: list[dict[str, object]] = field(default_factory=list)
    last_status: str = "never_run"

    @contextmanager
    def transaction(self) -> Iterator[None]:
        snapshot = deepcopy(self.__dict__)
        try:
            yield
        except BaseException:
            self.__dict__.update(snapshot)
            raise

    def watermark(self, asset: str) -> int | None:
        values = [ts for (stored_asset, ts) in self.candles if stored_asset == asset]
        hot = max(values) if values else None
        durable = self.durable_watermarks.get(asset)
        candidates = [value for value in (hot, durable) if value is not None]
        return max(candidates) if candidates else None

    def upsert_candles(self, candles: list[Candle], source: str) -> int:
        inserted = 0
        asset = _asset_from_source(source)
        for candle in candles:
            key = (asset, candle.ts)
            existing = self.candles.get(key)
            if existing is not None and existing[1] != source:
                raise RepositoryError("COL_SOURCE_CONFLICT")
            if existing is None:
                inserted += 1
            self.candles[key] = (candle, source)
            self.durable_watermarks[asset] = max(
                candle.ts, self.durable_watermarks.get(asset, candle.ts)
            )
        return inserted

    def record_gaps(self, asset: str, gaps: list[GapRecord]) -> None:
        existing = {(item.asset, item.from_ts, item.to_ts) for item in self.gaps}
        for gap in gaps:
            key = (gap.asset, gap.from_ts, gap.to_ts)
            if key not in existing:
                self.gaps.append(gap)
                existing.add(key)

    def upsert_payout(
        self,
        asset: str,
        hour_ts: int,
        value: Decimal,
        *,
        observed_at: int,
        source: str,
    ) -> None:
        if hour_ts != observed_at - observed_at % 3600:
            raise RepositoryError("COL_PAYOUT_OBSERVATION_INVALID")
        observation_key = (asset, observed_at)
        observation = PayoutObservation(asset, observed_at, value * Decimal(100), source)
        existing_observation = self.payout_observations.get(observation_key)
        if existing_observation is not None:
            if existing_observation != observation:
                raise RepositoryError("COL_PAYOUT_OBSERVATION_CONFLICT")
            return
        self.payout_observations[observation_key] = observation
        key = (asset, hour_ts)
        observations = [
            item
            for item in self.payout_observations.values()
            if item.asset == asset and item.observed_at - item.observed_at % 3600 == hour_ts
        ]
        total = sum((item.payout_pct for item in observations), Decimal("0"))
        self.payouts[key] = PayoutRecord(
            asset=asset,
            hour_ts=hour_ts,
            payout_pct=total / Decimal(len(observations)),
            samples=len(observations),
        )

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
