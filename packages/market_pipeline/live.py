from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from packages.domain.market import MarketTick
from packages.market_data import CandleEnvelope, CandleIngress, CandleIngressStatus, ClosedCandle
from packages.market_pipeline.clock import MonotonicClock
from packages.market_pipeline.dispatcher import (
    AcceptedCandleDispatcher,
    ShadowDecisionFingerprint,
)
from packages.market_pipeline.health import MarketHealthGate
from packages.market_pipeline.models import (
    MarketHealthReason,
    MarketPipelineMetrics,
    MarketSeriesId,
)
from packages.market_pipeline.scheduler import MarketBackfillScheduler
from packages.observability import EventSink, NullEventSink


class LiveAggregationStatus(StrEnum):
    ACCUMULATED = "ACCUMULATED"
    CLOSED = "CLOSED"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    GAPPED = "GAPPED"


@dataclass(frozen=True, slots=True)
class LiveAggregationResult:
    status: LiveAggregationStatus
    candle: ClosedCandle | None
    reason_code: str


@dataclass(slots=True)
class _OpenBucket:
    open_epoch: int
    open_units: int
    high_units: int
    low_units: int
    close_units: int
    first_tick_epoch: int
    last_tick_epoch: int
    subscription_id: str


def _epoch_ms(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("live tick received_at must be timezone-aware")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value.astimezone(UTC) - epoch
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


class ClosedCandleAggregator:
    """Builds fixed-timeframe candles from validated ticks without forward-fill."""

    def __init__(
        self,
        series_id: MarketSeriesId,
        *,
        price_scale: int,
        max_seen_ticks: int = 100_000,
    ) -> None:
        if price_scale <= 0 or max_seen_ticks <= 0:
            raise ValueError("live candle price scale must be positive")
        self._series = series_id
        self._price_scale = price_scale
        self._bucket: _OpenBucket | None = None
        self._max_seen_ticks = max_seen_ticks
        self._seen: set[tuple[int, Decimal, str]] = set()
        self._seen_order: deque[tuple[int, Decimal, str]] = deque()

    def ingest(self, tick: MarketTick) -> LiveAggregationResult:
        if (
            tick.broker is not self._series.broker
            or tick.broker_symbol != self._series.broker_symbol
        ):
            raise ValueError("live tick does not match aggregator series")
        identity = (tick.epoch, tick.quote, tick.subscription_id)
        if identity in self._seen:
            return LiveAggregationResult(
                LiveAggregationStatus.DUPLICATE,
                None,
                "LIVE_TICK_DUPLICATE",
            )
        self._remember(identity)
        units = self._units(tick.quote)
        timeframe = self._series.timeframe_seconds
        bucket_open = (tick.epoch // timeframe) * timeframe
        current = self._bucket
        if current is None:
            self._bucket = self._new_bucket(bucket_open, units, tick)
            return LiveAggregationResult(
                LiveAggregationStatus.ACCUMULATED,
                None,
                "LIVE_CANDLE_STARTED",
            )
        if bucket_open < current.open_epoch or tick.epoch < current.last_tick_epoch:
            return LiveAggregationResult(
                LiveAggregationStatus.OUT_OF_ORDER,
                None,
                "LIVE_TICK_OUT_OF_ORDER",
            )
        if bucket_open == current.open_epoch:
            current.high_units = max(current.high_units, units)
            current.low_units = min(current.low_units, units)
            current.close_units = units
            current.last_tick_epoch = tick.epoch
            return LiveAggregationResult(
                LiveAggregationStatus.ACCUMULATED,
                None,
                "LIVE_CANDLE_UPDATED",
            )
        candle = self._close(current, tick)
        self._bucket = self._new_bucket(bucket_open, units, tick)
        if bucket_open != current.open_epoch + timeframe:
            return LiveAggregationResult(
                LiveAggregationStatus.GAPPED,
                candle,
                "LIVE_CANDLE_GAP",
            )
        return LiveAggregationResult(
            LiveAggregationStatus.CLOSED,
            candle,
            "LIVE_CANDLE_CLOSED",
        )

    def reset(self) -> None:
        self._bucket = None
        self._seen.clear()
        self._seen_order.clear()

    @property
    def seen_tick_count(self) -> int:
        return len(self._seen)

    def _remember(self, identity: tuple[int, Decimal, str]) -> None:
        if len(self._seen_order) >= self._max_seen_ticks:
            self._seen.discard(self._seen_order.popleft())
        self._seen.add(identity)
        self._seen_order.append(identity)

    def _units(self, quote: Decimal) -> int:
        scaled = quote * self._price_scale
        integral = scaled.to_integral_value()
        if scaled != integral:
            raise ValueError("live tick precision exceeds configured price scale")
        return int(integral)

    def _new_bucket(self, bucket_open: int, units: int, tick: MarketTick) -> _OpenBucket:
        return _OpenBucket(
            open_epoch=bucket_open,
            open_units=units,
            high_units=units,
            low_units=units,
            close_units=units,
            first_tick_epoch=tick.epoch,
            last_tick_epoch=tick.epoch,
            subscription_id=tick.subscription_id,
        )

    def _close(self, bucket: _OpenBucket, evidence_tick: MarketTick) -> ClosedCandle:
        close_ms = (bucket.open_epoch + self._series.timeframe_seconds) * 1_000
        received_ms = _epoch_ms(evidence_tick.received_at)
        return ClosedCandle(
            broker=self._series.broker,
            symbol=self._series.broker_symbol,
            timeframe_seconds=self._series.timeframe_seconds,
            open_time_ms=bucket.open_epoch * 1_000,
            close_time_ms=close_ms,
            open_units=bucket.open_units,
            high_units=bucket.high_units,
            low_units=bucket.low_units,
            close_units=bucket.close_units,
            price_scale=self._price_scale,
            source="DERIV_TICKS_READ_ONLY",
            source_event_id="|".join(
                (
                    bucket.subscription_id,
                    str(bucket.first_tick_epoch),
                    str(bucket.last_tick_epoch),
                )
            ),
            source_timestamp_ms=close_ms,
            received_timestamp_ms=received_ms,
        )


class LiveTickSource(Protocol):
    def subscribe_market_ticks(self, symbol: str) -> MarketTick: ...

    def receive_market_tick(self, timeout: float) -> MarketTick | None: ...

    def unsubscribe_market_ticks(self, subscription_id: str) -> bool: ...


class ContinuousShadowRuntime:
    """Poll-driven live runtime. It cannot dispatch financial commands."""

    def __init__(
        self,
        series_id: MarketSeriesId,
        source: LiveTickSource,
        aggregator: ClosedCandleAggregator,
        ingress: CandleIngress,
        health: MarketHealthGate,
        scheduler: MarketBackfillScheduler,
        dispatcher: AcceptedCandleDispatcher,
        clock: MonotonicClock,
        *,
        fingerprint: ProtocolFingerprint | None = None,
        reference: ReferenceFingerprint | None = None,
        stale_after_seconds: float = 120.0,
        events: EventSink | None = None,
        metrics: MarketPipelineMetrics | None = None,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("live stale threshold must be positive")
        self._series = series_id
        self._source = source
        self._aggregator = aggregator
        self._ingress = ingress
        self._health = health
        self._scheduler = scheduler
        self._dispatcher = dispatcher
        self._clock = clock
        self._fingerprint = fingerprint
        self._reference = reference
        self._stale_after = stale_after_seconds
        self._events = events or NullEventSink()
        self.metrics = metrics or MarketPipelineMetrics()
        self._subscription_id: str | None = None
        self._last_tick_at: float | None = None

    @property
    def subscribed(self) -> bool:
        return self._subscription_id is not None

    def start(self) -> bool:
        self._scheduler.tick()
        return self._restore_subscription()

    def recover_and_restore(self) -> bool:
        self._scheduler.trigger(self._series)
        self._scheduler.tick()
        return self._restore_subscription()

    def poll_once(self, *, timeout: float) -> LiveAggregationResult | None:
        if timeout <= 0:
            raise ValueError("live poll timeout must be positive")
        if not self.subscribed or not self._health.snapshot(self._series).dispatch_allowed:
            return None
        try:
            tick = self._source.receive_market_tick(timeout)
        except Exception:
            generation = self.on_disconnect()
            self._events.emit(
                "shadow_stream_disconnected",
                reason_code=MarketHealthReason.RECONNECT_REQUIRED.value,
                series_id=self._series.key,
                reconnect_generation=generation,
            )
            raise
        if tick is None:
            self.metrics.live_poll_timeouts += 1
            if (
                self._last_tick_at is not None
                and self._clock.now() - self._last_tick_at > self._stale_after
            ):
                self._health.mark_suspended(self._series)
                self._discard_subscription(forget=True)
                self._aggregator.reset()
            return None
        return self._ingest_tick(tick)

    def on_disconnect(self) -> int:
        generation = self._health.start_reconnect(self._series)
        self.metrics.reconnect_count += 1
        self._discard_subscription(forget=False)
        self._aggregator.reset()
        return generation

    def stop(self) -> None:
        self._discard_subscription(forget=True)

    def _restore_subscription(self) -> bool:
        if not self._health.snapshot(self._series).dispatch_allowed:
            return False
        if self._subscription_id is not None:
            return True
        initial = self._source.subscribe_market_ticks(self._series.broker_symbol)
        self._subscription_id = initial.subscription_id
        self.metrics.subscription_restores += 1
        self._events.emit(
            "shadow_subscription_restored",
            series_id=self._series.key,
            subscription_id=initial.subscription_id,
        )
        self._ingest_tick(initial)
        return True

    def _discard_subscription(self, *, forget: bool) -> None:
        subscription_id = self._subscription_id
        self._subscription_id = None
        if not forget or subscription_id is None:
            return
        try:
            forgotten = self._source.unsubscribe_market_ticks(subscription_id)
        except Exception:
            forgotten = False
        if not forgotten:
            self._events.emit(
                "shadow_unsubscribe_failed",
                series_id=self._series.key,
                subscription_id=subscription_id,
            )

    def _ingest_tick(self, tick: MarketTick) -> LiveAggregationResult:
        self.metrics.live_ticks_received += 1
        self._last_tick_at = self._clock.now()
        result = self._aggregator.ingest(tick)
        if result.status is LiveAggregationStatus.DUPLICATE:
            self.metrics.live_ticks_duplicate += 1
            return result
        if result.status is LiveAggregationStatus.OUT_OF_ORDER:
            self.metrics.live_ticks_out_of_order += 1
            self.metrics.live_gap_count += 1
            self._health.mark_gap(self._series)
            self._scheduler.trigger(self._series)
            return result
        candle = result.candle
        if candle is None:
            return result
        ingress = self._ingress.ingest(CandleEnvelope.from_closed_candle(candle))
        if ingress.status is CandleIngressStatus.DUPLICATE:
            self.metrics.live_candles_duplicate += 1
        elif ingress.status is not CandleIngressStatus.ACCEPTED:
            self.metrics.live_gap_count += 1
            self._health.mark_gap(self._series)
            self._scheduler.trigger(self._series)
            return result
        if result.status is LiveAggregationStatus.GAPPED:
            self.metrics.live_gap_count += 1
            self._health.mark_gap(self._series)
            self._scheduler.trigger(self._series)
            return result
        if ingress.status is CandleIngressStatus.ACCEPTED and ingress.candle is not None:
            self._dispatcher.dispatch(self._series, ingress.candle)
            self.metrics.live_candles_closed += 1
            lag = max(0, ingress.candle.received_timestamp_ms - ingress.candle.close_time_ms)
            self.metrics.live_dispatch_lag_ms_total += lag
            self.metrics.live_dispatch_lag_ms_max = max(
                self.metrics.live_dispatch_lag_ms_max,
                lag,
            )
            self._compare(ingress.candle)
        return result

    def _compare(self, candle: ClosedCandle) -> None:
        if self._fingerprint is None or self._reference is None:
            return
        expected = self._reference(candle.close_time_ms)
        if expected is None:
            return
        actual = self._fingerprint()
        self.metrics.live_replay_comparisons += 1
        if actual != expected:
            self.metrics.live_replay_divergences += 1
            self._health.mark_failed(self._series, MarketHealthReason.SHADOW_DIVERGENCE)
            self._events.emit(
                "shadow_replay_divergence",
                reason_code=MarketHealthReason.SHADOW_DIVERGENCE.value,
                series_id=self._series.key,
                candle_id=candle.candle_id,
            )


class ProtocolFingerprint(Protocol):
    def __call__(self) -> ShadowDecisionFingerprint: ...


class ReferenceFingerprint(Protocol):
    def __call__(self, close_time_ms: int) -> ShadowDecisionFingerprint | None: ...
