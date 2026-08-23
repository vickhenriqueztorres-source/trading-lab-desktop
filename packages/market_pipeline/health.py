from __future__ import annotations

import threading
from dataclasses import dataclass

from packages.domain.models import Broker
from packages.market_pipeline.models import (
    BrokerMarketHealth,
    MarketHealthReason,
    MarketPipelineHealthSnapshot,
    MarketSeriesHealth,
    MarketSeriesId,
)
from packages.observability import EventSink, NullEventSink


@dataclass(slots=True)
class _SeriesState:
    health: MarketSeriesHealth
    reason: MarketHealthReason
    required: int
    durable: int = 0
    last_close: int | None = None
    last_source_event: str | None = None
    gap_count: int = 0
    backpressure: bool = False
    generation: int = 0


class MarketHealthGate:
    _BROKER_PRIORITY = (
        MarketSeriesHealth.FAILED,
        MarketSeriesHealth.INCOMPATIBLE,
        MarketSeriesHealth.CLOCK_UNTRUSTED,
        MarketSeriesHealth.BACKPRESSURED,
        MarketSeriesHealth.GAPPED,
        MarketSeriesHealth.RECONNECTING,
        MarketSeriesHealth.STALE,
        MarketSeriesHealth.WARMING_UP,
        MarketSeriesHealth.INITIALIZING,
        MarketSeriesHealth.HEALTHY,
    )

    def __init__(self, events: EventSink | None = None) -> None:
        self._lock = threading.Lock()
        self._states: dict[MarketSeriesId, _SeriesState] = {}
        self._events = events or NullEventSink()

    def register(self, series_id: MarketSeriesId, *, required_closed_candles: int) -> None:
        if required_closed_candles <= 0:
            raise ValueError("market warmup requirement must be positive")
        with self._lock:
            if series_id in self._states:
                if self._states[series_id].required != required_closed_candles:
                    raise ValueError("market series already has another warmup requirement")
                return
            self._states[series_id] = _SeriesState(
                MarketSeriesHealth.INITIALIZING,
                MarketHealthReason.INITIAL_WARMUP,
                required_closed_candles,
            )
        self._emit_change(
            series_id,
            MarketSeriesHealth.INITIALIZING,
            MarketHealthReason.INITIAL_WARMUP,
        )

    def snapshot(self, series_id: MarketSeriesId) -> MarketPipelineHealthSnapshot:
        with self._lock:
            state = self._require(series_id)
            return MarketPipelineHealthSnapshot(
                series_id=series_id,
                health=state.health,
                reason=state.reason,
                last_durable_close=state.last_close,
                last_source_event=state.last_source_event,
                gap_count=state.gap_count,
                backpressure_active=state.backpressure,
                reconnect_generation=state.generation,
                warmup_progress=min(state.durable, state.required),
                warmup_required=state.required,
                dispatch_allowed=state.health is MarketSeriesHealth.HEALTHY,
            )

    def broker_snapshot(self, broker: Broker) -> BrokerMarketHealth:
        with self._lock:
            states = [state for series, state in self._states.items() if series.broker is broker]
            if not states:
                return BrokerMarketHealth(broker, MarketSeriesHealth.INITIALIZING, 0, 0)
            health = next(
                candidate
                for candidate in self._BROKER_PRIORITY
                if any(state.health is candidate for state in states)
            )
            blocked = sum(state.health is not MarketSeriesHealth.HEALTHY for state in states)
            return BrokerMarketHealth(broker, health, len(states), blocked)

    def mark_warming_up(
        self,
        series_id: MarketSeriesId,
        *,
        durable_closed_candles: int,
        last_durable_close: int | None,
        last_source_event: str | None,
    ) -> None:
        self._transition(
            series_id,
            MarketSeriesHealth.WARMING_UP,
            MarketHealthReason.INITIAL_WARMUP,
            durable=durable_closed_candles,
            last_close=last_durable_close,
            last_source_event=last_source_event,
        )

    def mark_gap(self, series_id: MarketSeriesId) -> None:
        with self._lock:
            state = self._require(series_id)
            state.gap_count += 1
        self._transition(series_id, MarketSeriesHealth.GAPPED, MarketHealthReason.GAP_DETECTED)

    def mark_backpressure(self, series_id: MarketSeriesId) -> None:
        with self._lock:
            self._require(series_id).backpressure = True
        self._transition(
            series_id,
            MarketSeriesHealth.BACKPRESSURED,
            MarketHealthReason.BACKPRESSURE,
        )

    def mark_clock_untrusted(self, series_id: MarketSeriesId) -> None:
        self._transition(
            series_id,
            MarketSeriesHealth.CLOCK_UNTRUSTED,
            MarketHealthReason.CLOCK_UNTRUSTED,
        )

    def mark_failed(
        self,
        series_id: MarketSeriesId,
        reason: MarketHealthReason = MarketHealthReason.HISTORY_EXHAUSTED,
    ) -> None:
        self._transition(series_id, MarketSeriesHealth.FAILED, reason)

    def mark_incompatible(self, series_id: MarketSeriesId) -> None:
        self._transition(
            series_id,
            MarketSeriesHealth.INCOMPATIBLE,
            MarketHealthReason.SCHEMA_INCOMPATIBLE,
        )

    def start_reconnect(self, series_id: MarketSeriesId) -> int:
        with self._lock:
            state = self._require(series_id)
            state.generation += 1
            generation = state.generation
        self._transition(
            series_id,
            MarketSeriesHealth.RECONNECTING,
            MarketHealthReason.RECONNECT_REQUIRED,
        )
        self._events.emit(
            "market_reconnect_started",
            series_id=series_id.key,
            reconnect_generation=generation,
        )
        return generation

    def mark_suspended(self, series_id: MarketSeriesId) -> int:
        with self._lock:
            state = self._require(series_id)
            state.generation += 1
            generation = state.generation
        self._transition(series_id, MarketSeriesHealth.STALE, MarketHealthReason.SOURCE_STALE)
        return generation

    def complete_recovery(
        self,
        series_id: MarketSeriesId,
        *,
        generation: int,
        continuity_valid: bool,
        clock_trusted: bool,
        durable_closed_candles: int,
        last_durable_close: int | None,
        last_source_event: str | None,
    ) -> bool:
        with self._lock:
            state = self._require(series_id)
            previous_health = state.health
            if generation != state.generation:
                self._events.emit(
                    "backfill_failed",
                    reason_code=MarketHealthReason.STALE_CONNECTION_RESPONSE.value,
                    series_id=series_id.key,
                    reconnect_generation=generation,
                )
                return False
            if not clock_trusted:
                target = MarketSeriesHealth.CLOCK_UNTRUSTED
                reason = MarketHealthReason.CLOCK_UNTRUSTED
            elif not continuity_valid:
                target = MarketSeriesHealth.GAPPED
                reason = MarketHealthReason.CONTINUITY_UNPROVEN
            elif durable_closed_candles < state.required:
                target = MarketSeriesHealth.WARMING_UP
                reason = MarketHealthReason.INITIAL_WARMUP
            else:
                target = MarketSeriesHealth.HEALTHY
                reason = MarketHealthReason.HEALTHY
                state.gap_count = 0
                state.backpressure = False
            state.durable = durable_closed_candles
            state.last_close = last_durable_close
            state.last_source_event = last_source_event
        self._transition(series_id, target, reason)
        if target is MarketSeriesHealth.HEALTHY:
            if previous_health is MarketSeriesHealth.GAPPED:
                self._events.emit(
                    "market_gap_resolved",
                    series_id=series_id.key,
                    reconnect_generation=generation,
                )
            if previous_health is MarketSeriesHealth.RECONNECTING:
                self._events.emit(
                    "market_reconnect_completed",
                    series_id=series_id.key,
                    reconnect_generation=generation,
                )
        return target is MarketSeriesHealth.HEALTHY

    def _transition(
        self,
        series_id: MarketSeriesId,
        health: MarketSeriesHealth,
        reason: MarketHealthReason,
        *,
        durable: int | None = None,
        last_close: int | None = None,
        last_source_event: str | None = None,
    ) -> None:
        with self._lock:
            state = self._require(series_id)
            changed = state.health is not health or state.reason is not reason
            state.health = health
            state.reason = reason
            if durable is not None:
                state.durable = durable
            if last_close is not None:
                state.last_close = last_close
            if last_source_event is not None:
                state.last_source_event = last_source_event
        if changed:
            self._emit_change(series_id, health, reason)

    def _require(self, series_id: MarketSeriesId) -> _SeriesState:
        try:
            return self._states[series_id]
        except KeyError as exc:
            raise KeyError("market series is not registered") from exc

    def _emit_change(
        self,
        series_id: MarketSeriesId,
        health: MarketSeriesHealth,
        reason: MarketHealthReason,
    ) -> None:
        self._events.emit(
            "market_health_changed",
            reason_code=reason.value,
            series_id=series_id.key,
            health=health.value,
        )
