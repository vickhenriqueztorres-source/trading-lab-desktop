"""Shared IQ Option candle-series management for local strategy evaluation.

This module is intentionally data-only.  It owns no order state, performs no
financial action and exposes no submit/buy API.  The auto trader consumes
immutable snapshots from here and still routes every order through the Core
financial pipeline.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Protocol

from apps.core.iqoption_connection_safety import (
    IQOptionMessageBudget,
    IQOptionMessageBudgetDecision,
)
from packages.domain.market import MarketCandle
from packages.domain.models import Broker

IQOPTION_SERIES_PRODUCT = "BINARY_OPTION"
IQOPTION_SERIES_MAX_FETCH_COUNT = 1_000
IQOPTION_SERIES_BOOTSTRAP_OVERLAP = 3
IQOPTION_SERIES_STABLE_FETCHES_PER_MINUTE_16_ASSETS_3_TFS = "20.2667"


class IQOptionSeriesReason(StrEnum):
    OK = "OK"
    MESSAGE_BUDGET_EXHAUSTED = "MESSAGE_BUDGET_EXHAUSTED"
    SERIES_QUEUE_FULL = "SERIES_QUEUE_FULL"
    WARMUP_CAPACITY_EXCEEDED = "WARMUP_CAPACITY_EXCEEDED"
    MARKET_HISTORY_UNAVAILABLE = "MARKET_HISTORY_UNAVAILABLE"
    TARGET_CANDLE_UNAVAILABLE = "TARGET_CANDLE_UNAVAILABLE"


class IQOptionSeriesPriority(IntEnum):
    RECOVERY = 0
    BOOTSTRAP = 1
    STEADY = 2


class IQOptionMarketHistoryClient(Protocol):
    def market_history(
        self,
        symbol: str,
        *,
        style: str,
        count: int,
        timeframe_seconds: int,
    ) -> tuple[Sequence[object], Sequence[MarketCandle]]:
        """Return broker history; implemented by the guarded IQ worker client."""


type IQOptionSeriesFetcher = Callable[
    [IQOptionMarketHistoryClient, IQOptionSeriesKey, int], Sequence[MarketCandle]
]


@dataclass(frozen=True, slots=True)
class IQOptionSeriesKey:
    """Exact identity of one candle stream.

    The key deliberately includes account/session generation and the exact
    broker symbol.  ``EURUSD`` and ``EURUSD-OTC`` are different series.
    """

    broker: Broker
    account_id: str
    product: str
    generation: str
    asset: str
    timeframe_seconds: int

    def __post_init__(self) -> None:
        if self.broker is not Broker.IQ_OPTION:
            raise ValueError("IQ Option series key must use Broker.IQ_OPTION")
        if not self.account_id.strip():
            raise ValueError("IQ Option series account_id is required")
        if not self.product.strip():
            raise ValueError("IQ Option series product is required")
        if not self.generation.strip():
            raise ValueError("IQ Option series generation is required")
        if not self.asset.strip():
            raise ValueError("IQ Option series asset is required")
        if self.timeframe_seconds <= 0:
            raise ValueError("IQ Option series timeframe must be positive")


@dataclass(frozen=True, slots=True)
class IQOptionSeriesRequest:
    key: IQOptionSeriesKey
    warmup_required: int
    close_epoch: int
    priority: IQOptionSeriesPriority = IQOptionSeriesPriority.STEADY


@dataclass(frozen=True, slots=True)
class IQOptionSeriesSnapshot:
    key: IQOptionSeriesKey
    candles: tuple[MarketCandle, ...]
    close_epoch: int
    warmup_required: int
    complete_warmup: bool
    gaps_detected: int
    corrections_detected: int


@dataclass(frozen=True, slots=True)
class IQOptionSeriesFetchOutcome:
    reason: IQOptionSeriesReason
    snapshot: IQOptionSeriesSnapshot | None
    budget: IQOptionMessageBudgetDecision | None = None

    @property
    def ok(self) -> bool:
        return self.reason is IQOptionSeriesReason.OK and self.snapshot is not None


@dataclass(frozen=True, slots=True)
class IQOptionSeriesStats:
    requests: int = 0
    fetches_sent: int = 0
    dedup_hits: int = 0
    budget_denied: int = 0
    queue_full: int = 0
    warmup_capacity_rejected: int = 0
    partial_rejected: int = 0
    incompatible_rejected: int = 0
    duplicates_rejected: int = 0
    out_of_order_batches: int = 0
    gaps_detected: int = 0
    corrections_detected: int = 0


class IQOptionSeriesHub:
    """Bounded shared manager for IQ Option market-data series.

    Local message-budget policy:
    * total application ceiling: 90 messages/minute;
    * market-data ceiling used here: 60 messages/minute;
    * stable target for 16 assets × 3 timeframes is 20.2667 fetches/minute
      when each series is refreshed at its close cadence.

    The financial/recovery reserve is not borrowed for warmup or scanning.
    """

    def __init__(
        self,
        *,
        message_budget: IQOptionMessageBudget,
        monotonic: Callable[[], float],
        utc_clock: Callable[[], datetime],
        max_fetch_count: int = IQOPTION_SERIES_MAX_FETCH_COUNT,
        bootstrap_overlap: int = IQOPTION_SERIES_BOOTSTRAP_OVERLAP,
        max_queue_size: int = 128,
    ) -> None:
        if max_fetch_count <= 0:
            raise ValueError("IQ Option series fetch capacity must be positive")
        if bootstrap_overlap < 0:
            raise ValueError("IQ Option series bootstrap overlap cannot be negative")
        if max_queue_size < 0:
            raise ValueError("IQ Option series queue size cannot be negative")
        self._message_budget = message_budget
        self._monotonic = monotonic
        self._utc_clock = utc_clock
        self._max_fetch_count = max_fetch_count
        self._bootstrap_overlap = bootstrap_overlap
        self._max_queue_size = max_queue_size
        self._snapshots: dict[tuple[IQOptionSeriesKey, int], IQOptionSeriesSnapshot] = {}
        self._latest_by_key: dict[IQOptionSeriesKey, IQOptionSeriesSnapshot] = {}
        self._last_served: dict[IQOptionSeriesKey, int] = {}
        self._service_counter = 0
        self._stats = IQOptionSeriesStats()

    @property
    def stats(self) -> IQOptionSeriesStats:
        return self._stats

    @property
    def active_series_count(self) -> int:
        """Number of exact broker/account/generation series currently cached."""
        return len(self._latest_by_key)

    def invalidate(self, *, reason: str) -> None:
        """Drop cached projections after manifest/session/account changes."""

        if not reason.strip():
            raise ValueError("IQ Option series invalidation reason is required")
        self._snapshots.clear()
        self._latest_by_key.clear()
        self._last_served.clear()
        self._service_counter = 0

    def replace_message_budget(self, message_budget: IQOptionMessageBudget) -> None:
        """Swap the budget object used by tests or lifecycle reconfiguration."""

        self._message_budget = message_budget

    def close_epoch(self, timeframe_seconds: int) -> int:
        if timeframe_seconds <= 0:
            raise ValueError("IQ Option series timeframe must be positive")
        now = self._utc_clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("IQ Option series clock must be timezone aware")
        return int(now.astimezone(UTC).timestamp()) // timeframe_seconds

    def schedule(
        self,
        requests: Sequence[IQOptionSeriesRequest],
    ) -> tuple[IQOptionSeriesRequest, ...]:
        """Deduplicate and order requests by priority/deadline with fairness."""

        deduped: dict[tuple[IQOptionSeriesKey, int], IQOptionSeriesRequest] = {}
        for request in requests:
            self._validate_request(request)
            key = (request.key, request.close_epoch)
            current = deduped.get(key)
            if current is None or request.priority < current.priority:
                deduped[key] = request

        ordered = sorted(
            deduped.values(),
            key=lambda item: (
                int(item.priority),
                item.close_epoch,
                self._last_served.get(item.key, -1),
                item.key.asset,
                item.key.timeframe_seconds,
            ),
        )
        accepted = tuple(ordered[: self._max_queue_size])
        if len(ordered) > len(accepted):
            self._increment(queue_full=len(ordered) - len(accepted))
        for item in accepted:
            self._service_counter += 1
            self._last_served[item.key] = self._service_counter
        return accepted

    def snapshot(
        self,
        *,
        client: IQOptionMarketHistoryClient,
        key: IQOptionSeriesKey,
        warmup_required: int,
        close_epoch: int | None = None,
        priority: IQOptionSeriesPriority = IQOptionSeriesPriority.STEADY,
        fetcher: IQOptionSeriesFetcher | None = None,
        required_close_time: datetime | None = None,
    ) -> IQOptionSeriesFetchOutcome:
        request = IQOptionSeriesRequest(
            key=key,
            warmup_required=warmup_required,
            close_epoch=self.close_epoch(key.timeframe_seconds)
            if close_epoch is None
            else close_epoch,
            priority=priority,
        )
        self._validate_request(request)
        self._increment(requests=1)

        required_close_epoch = (
            None
            if required_close_time is None
            else self._datetime_epoch(required_close_time, "required_close_time")
        )
        cached = self._snapshots.get((key, request.close_epoch))
        if (
            cached is not None
            and cached.warmup_required >= warmup_required
            and self._contains_close_epoch(cached.candles, required_close_epoch)
        ):
            self._increment(dedup_hits=1)
            return IQOptionSeriesFetchOutcome(IQOptionSeriesReason.OK, cached)

        if self._max_queue_size == 0:
            self._increment(queue_full=1)
            return IQOptionSeriesFetchOutcome(IQOptionSeriesReason.SERIES_QUEUE_FULL, None)

        fetch_count = warmup_required + self._bootstrap_overlap
        if fetch_count > self._max_fetch_count:
            self._increment(warmup_capacity_rejected=1)
            return IQOptionSeriesFetchOutcome(
                IQOptionSeriesReason.WARMUP_CAPACITY_EXCEEDED,
                None,
            )

        budget = self._message_budget.try_acquire(self._monotonic())
        if not budget.allowed:
            self._increment(budget_denied=1)
            return IQOptionSeriesFetchOutcome(
                IQOptionSeriesReason.MESSAGE_BUDGET_EXHAUSTED,
                None,
                budget,
            )

        if fetcher is None:
            _ticks, raw_candles = client.market_history(
                key.asset,
                style="candles",
                count=fetch_count,
                timeframe_seconds=key.timeframe_seconds,
            )
        else:
            raw_candles = fetcher(client, key, fetch_count)
        previous = self._latest_by_key.get(key)
        candles, batch_stats = self._validated_candles(key, raw_candles, previous)
        snapshot = IQOptionSeriesSnapshot(
            key=key,
            candles=candles,
            close_epoch=request.close_epoch,
            warmup_required=warmup_required,
            complete_warmup=len(candles) >= warmup_required,
            gaps_detected=batch_stats.gaps_detected,
            corrections_detected=batch_stats.corrections_detected,
        )
        if not self._contains_close_epoch(snapshot.candles, required_close_epoch):
            # A request at the minute boundary can legitimately receive the
            # preceding candle. Do not cache that response as proof that the
            # recovery candle was observed; the next bounded poll must fetch.
            self._latest_by_key[key] = snapshot
            self._increment(
                fetches_sent=1,
                partial_rejected=batch_stats.partial_rejected,
                incompatible_rejected=batch_stats.incompatible_rejected,
                duplicates_rejected=batch_stats.duplicates_rejected,
                out_of_order_batches=batch_stats.out_of_order_batches,
                gaps_detected=batch_stats.gaps_detected,
                corrections_detected=batch_stats.corrections_detected,
            )
            return IQOptionSeriesFetchOutcome(
                IQOptionSeriesReason.TARGET_CANDLE_UNAVAILABLE,
                None,
                budget,
            )
        self._snapshots[(key, request.close_epoch)] = snapshot
        self._latest_by_key[key] = snapshot
        self._increment(
            fetches_sent=1,
            partial_rejected=batch_stats.partial_rejected,
            incompatible_rejected=batch_stats.incompatible_rejected,
            duplicates_rejected=batch_stats.duplicates_rejected,
            out_of_order_batches=batch_stats.out_of_order_batches,
            gaps_detected=batch_stats.gaps_detected,
            corrections_detected=batch_stats.corrections_detected,
        )
        return IQOptionSeriesFetchOutcome(IQOptionSeriesReason.OK, snapshot, budget)

    def _validated_candles(
        self,
        key: IQOptionSeriesKey,
        raw_candles: Sequence[MarketCandle],
        previous: IQOptionSeriesSnapshot | None,
    ) -> tuple[tuple[MarketCandle, ...], IQOptionSeriesStats]:
        partial_rejected = 0
        incompatible_rejected = 0
        duplicates_rejected = 0
        corrections_detected = 0
        by_close: dict[int, MarketCandle] = {}
        source_order: list[int] = []

        for candle in raw_candles:
            close_epoch = self._candle_close_epoch(candle)
            source_order.append(close_epoch)
            if not candle.is_closed:
                partial_rejected += 1
                continue
            if (
                candle.broker is not key.broker
                or candle.broker_symbol != key.asset
                or candle.timeframe_seconds != key.timeframe_seconds
            ):
                incompatible_rejected += 1
                continue
            existing = by_close.get(close_epoch)
            if existing is not None:
                if self._same_candle(existing, candle):
                    duplicates_rejected += 1
                    continue
                corrections_detected += 1
            by_close[close_epoch] = candle

        out_of_order_batches = 1 if source_order != sorted(source_order) else 0
        ordered = tuple(by_close[epoch] for epoch in sorted(by_close))
        gaps_detected = self._count_gaps(ordered, key.timeframe_seconds)
        if previous is not None:
            previous_by_close = {
                self._candle_close_epoch(candle): candle for candle in previous.candles
            }
            for candle in ordered:
                old = previous_by_close.get(self._candle_close_epoch(candle))
                if old is not None and not self._same_candle(old, candle):
                    corrections_detected += 1

        return ordered, IQOptionSeriesStats(
            partial_rejected=partial_rejected,
            incompatible_rejected=incompatible_rejected,
            duplicates_rejected=duplicates_rejected,
            out_of_order_batches=out_of_order_batches,
            gaps_detected=gaps_detected,
            corrections_detected=corrections_detected,
        )

    @staticmethod
    def _validate_request(request: IQOptionSeriesRequest) -> None:
        if request.warmup_required <= 0:
            raise ValueError("IQ Option series warmup must be positive")
        if request.close_epoch < 0:
            raise ValueError("IQ Option series close epoch is invalid")

    @staticmethod
    def _candle_close_epoch(candle: MarketCandle) -> int:
        close_time = candle.close_time
        if close_time.tzinfo is None or close_time.utcoffset() is None:
            raise ValueError("IQ Option candle close_time must be timezone aware")
        value = close_time.astimezone(UTC).timestamp()
        if not math.isfinite(value) or value < 0:
            raise ValueError("IQ Option candle close_time is invalid")
        return int(value)

    @staticmethod
    def _datetime_epoch(value: datetime, field: str) -> int:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"IQ Option {field} must be timezone aware")
        epoch = value.astimezone(UTC).timestamp()
        if not math.isfinite(epoch) or epoch < 0:
            raise ValueError(f"IQ Option {field} is invalid")
        return int(epoch)

    @classmethod
    def _contains_close_epoch(
        cls,
        candles: Sequence[MarketCandle],
        required_close_epoch: int | None,
    ) -> bool:
        return required_close_epoch is None or any(
            cls._candle_close_epoch(candle) == required_close_epoch for candle in candles
        )

    @staticmethod
    def _same_candle(left: MarketCandle, right: MarketCandle) -> bool:
        return (
            left.open_time == right.open_time
            and left.close_time == right.close_time
            and left.open == right.open
            and left.high == right.high
            and left.low == right.low
            and left.close == right.close
            and left.tick_volume == right.tick_volume
        )

    @classmethod
    def _count_gaps(cls, candles: Sequence[MarketCandle], timeframe_seconds: int) -> int:
        gaps = 0
        previous_epoch: int | None = None
        for candle in candles:
            epoch = cls._candle_close_epoch(candle)
            if previous_epoch is not None:
                delta = epoch - previous_epoch
                if delta > timeframe_seconds:
                    gaps += max(1, delta // timeframe_seconds - 1)
            previous_epoch = epoch
        return gaps

    def _increment(
        self,
        *,
        requests: int = 0,
        fetches_sent: int = 0,
        dedup_hits: int = 0,
        budget_denied: int = 0,
        queue_full: int = 0,
        warmup_capacity_rejected: int = 0,
        partial_rejected: int = 0,
        incompatible_rejected: int = 0,
        duplicates_rejected: int = 0,
        out_of_order_batches: int = 0,
        gaps_detected: int = 0,
        corrections_detected: int = 0,
    ) -> None:
        self._stats = IQOptionSeriesStats(
            requests=self._stats.requests + requests,
            fetches_sent=self._stats.fetches_sent + fetches_sent,
            dedup_hits=self._stats.dedup_hits + dedup_hits,
            budget_denied=self._stats.budget_denied + budget_denied,
            queue_full=self._stats.queue_full + queue_full,
            warmup_capacity_rejected=(
                self._stats.warmup_capacity_rejected + warmup_capacity_rejected
            ),
            partial_rejected=self._stats.partial_rejected + partial_rejected,
            incompatible_rejected=self._stats.incompatible_rejected + incompatible_rejected,
            duplicates_rejected=self._stats.duplicates_rejected + duplicates_rejected,
            out_of_order_batches=self._stats.out_of_order_batches + out_of_order_batches,
            gaps_detected=self._stats.gaps_detected + gaps_detected,
            corrections_detected=self._stats.corrections_detected + corrections_detected,
        )


__all__ = [
    "IQOPTION_SERIES_BOOTSTRAP_OVERLAP",
    "IQOPTION_SERIES_MAX_FETCH_COUNT",
    "IQOPTION_SERIES_PRODUCT",
    "IQOPTION_SERIES_STABLE_FETCHES_PER_MINUTE_16_ASSETS_3_TFS",
    "IQOptionMarketHistoryClient",
    "IQOptionSeriesFetchOutcome",
    "IQOptionSeriesHub",
    "IQOptionSeriesKey",
    "IQOptionSeriesPriority",
    "IQOptionSeriesReason",
    "IQOptionSeriesRequest",
    "IQOptionSeriesSnapshot",
    "IQOptionSeriesStats",
]
