from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from packages.brokers.deriv import DerivCandleHistoryPump, DerivCandlePumpError
from packages.market_data import ClosedCandle
from packages.market_pipeline.dispatcher import AcceptedCandleDispatcher
from packages.market_pipeline.health import MarketHealthGate
from packages.market_pipeline.models import (
    MarketHealthReason,
    MarketPipelineMetrics,
    MarketSeriesHealth,
    MarketSeriesId,
    TrustedClosedHorizon,
)
from packages.market_pipeline.planner import BackfillPlanner
from packages.market_pipeline.scheduler import BackfillJobResult
from packages.observability import EventSink, NullEventSink
from packages.persistence.candle_repository import CandleRepository


@dataclass(frozen=True, slots=True)
class _SeriesConfig:
    required_closed_candles: int


class MarketBackfillCoordinator:
    """Coordinates only read-only recovery, durable continuity and optional shadow delivery."""

    def __init__(
        self,
        repository: CandleRepository,
        planner: BackfillPlanner,
        pump: DerivCandleHistoryPump,
        health: MarketHealthGate,
        horizon_provider: Callable[[MarketSeriesId], TrustedClosedHorizon | None],
        *,
        dispatcher: AcceptedCandleDispatcher | None = None,
        dispatch_cursor: Callable[[MarketSeriesId], int | None] = lambda _series: None,
        max_pages_per_recovery: int = 32,
        events: EventSink | None = None,
        metrics: MarketPipelineMetrics | None = None,
    ) -> None:
        if max_pages_per_recovery <= 0:
            raise ValueError("backfill recovery page limit must be positive")
        self._repository = repository
        self._planner = planner
        self._pump = pump
        self._health = health
        self._horizon_provider = horizon_provider
        self._dispatcher = dispatcher
        self._dispatch_cursor = dispatch_cursor
        self._max_pages = max_pages_per_recovery
        self._events = events or NullEventSink()
        self.metrics = metrics or MarketPipelineMetrics()
        self._configs: dict[MarketSeriesId, _SeriesConfig] = {}

    def register(self, series_id: MarketSeriesId, *, required_closed_candles: int) -> None:
        if required_closed_candles <= 0:
            raise ValueError("required closed candle count must be positive")
        existing = self._configs.get(series_id)
        config = _SeriesConfig(required_closed_candles)
        if existing is not None and existing != config:
            raise ValueError("market recovery series configuration changed")
        self._configs[series_id] = config
        self._health.register(series_id, required_closed_candles=required_closed_candles)

    def recover(self, series_id: MarketSeriesId, generation: int) -> BackfillJobResult:
        config = self._configs[series_id]
        previous_health = self._health.snapshot(series_id).health
        if previous_health is MarketSeriesHealth.INITIALIZING:
            self._events.emit("strategy_warmup_started", series_id=series_id.key)
        horizon = self._horizon_provider(series_id)
        if horizon is None:
            self._health.mark_clock_untrusted(series_id)
            self._events.emit(
                "market_clock_untrusted",
                reason_code=MarketHealthReason.CLOCK_UNTRUSTED.value,
                series_id=series_id.key,
            )
            return BackfillJobResult(
                generation,
                False,
                self._last_close(series_id),
                True,
                MarketHealthReason.CLOCK_UNTRUSTED,
            )
        last_source_event: str | None = None
        completed = False
        performed_request = False
        for _page in range(self._max_pages):
            candles = self._candles(series_id)
            last_close = candles[-1].close_time_ms if candles else None
            plan = self._planner.plan(
                series_id,
                generation=generation,
                horizon=horizon,
                last_durable_close_ms=last_close,
                durable_closed_candles=len(candles),
                required_closed_candles=config.required_closed_candles,
                force_overlap=(
                    not performed_request
                    and self._health.snapshot(series_id).health is not MarketSeriesHealth.HEALTHY
                ),
            )
            if plan is None:
                completed = True
                break
            self._health.mark_warming_up(
                series_id,
                durable_closed_candles=len(candles),
                last_durable_close=last_close,
                last_source_event=last_source_event,
            )
            try:
                self.metrics.backfill_requests += 1
                report = self._pump.backfill(
                    series_id.broker_symbol,
                    series_id.timeframe_seconds,
                    count=plan.count,
                    end_epoch=plan.end_epoch_seconds,
                )
            except DerivCandlePumpError as exc:
                if exc.reason_code in {
                    "DERIV_CANDLE_BACKPRESSURE",
                    "DERIV_CANDLE_BATCH_OVERFLOW",
                }:
                    self.metrics.backpressure_count += 1
                    self._health.mark_backpressure(series_id)
                    self._events.emit(
                        "market_backpressure_detected",
                        reason_code=MarketHealthReason.BACKPRESSURE.value,
                        series_id=series_id.key,
                    )
                raise
            performed_request = True
            self.metrics.backfill_candles_received += report.received_count
            self.metrics.backfill_duplicates += report.duplicate_count
            self.metrics.partial_candles_received += report.partial_count
            last_source_event = report.response_message_id
            self._events.emit(
                "backfill_batch_received",
                series_id=series_id.key,
                response_message_id=report.response_message_id,
                correlation_id=report.correlation_id,
                received_count=report.received_count,
                duplicate_count=report.duplicate_count,
            )
            if report.duplicate_count:
                self._events.emit(
                    "backfill_batch_duplicate",
                    series_id=series_id.key,
                    duplicate_count=report.duplicate_count,
                )
            if self._health.snapshot(series_id).reconnect_generation != generation:
                return BackfillJobResult(
                    generation,
                    False,
                    self._last_close(series_id),
                    True,
                    MarketHealthReason.STALE_CONNECTION_RESPONSE,
                )
            if report.has_quality_failure:
                self.metrics.gap_count += 1
                self._health.mark_gap(series_id)
                self._events.emit(
                    "market_gap_detected",
                    reason_code=MarketHealthReason.GAP_DETECTED.value,
                    series_id=series_id.key,
                )
                return BackfillJobResult(
                    generation,
                    False,
                    self._last_close(series_id),
                    True,
                    MarketHealthReason.GAP_DETECTED,
                )
        candles = self._candles(series_id)
        continuity_valid = completed and self._continuity_valid(
            candles,
            required=config.required_closed_candles,
            horizon_ms=horizon.close_epoch_ms,
        )
        healthy = self._health.complete_recovery(
            series_id,
            generation=generation,
            continuity_valid=continuity_valid,
            clock_trusted=True,
            durable_closed_candles=len(candles),
            last_durable_close=candles[-1].close_time_ms if candles else None,
            last_source_event=last_source_event,
        )
        if not healthy:
            return BackfillJobResult(
                generation,
                False,
                candles[-1].close_time_ms if candles else None,
                True,
                MarketHealthReason.CONTINUITY_UNPROVEN,
            )
        if previous_health is MarketSeriesHealth.GAPPED:
            self.metrics.gap_recovery_count += 1
        if previous_health is MarketSeriesHealth.RECONNECTING:
            self.metrics.reconnect_count += 1
        self.metrics.warmup_candles_loaded = max(
            self.metrics.warmup_candles_loaded,
            min(len(candles), config.required_closed_candles),
        )
        if previous_health in {
            MarketSeriesHealth.INITIALIZING,
            MarketSeriesHealth.WARMING_UP,
        }:
            self._events.emit(
                "strategy_warmup_completed",
                series_id=series_id.key,
                candle_count=min(len(candles), config.required_closed_candles),
            )
        self._dispatch_durable(series_id, candles)
        return BackfillJobResult(
            generation,
            True,
            candles[-1].close_time_ms if candles else None,
            False,
            MarketHealthReason.INITIAL_WARMUP,
        )

    def _dispatch_durable(
        self,
        series_id: MarketSeriesId,
        candles: tuple[ClosedCandle, ...],
    ) -> None:
        if self._dispatcher is None:
            return
        cursor = self._dispatch_cursor(series_id)
        for candle in candles:
            if cursor is None or candle.close_time_ms > cursor:
                self._dispatcher.dispatch(series_id, candle)

    def _candles(self, series_id: MarketSeriesId) -> tuple[ClosedCandle, ...]:
        return self._repository.range(
            (series_id.broker, series_id.broker_symbol, series_id.timeframe_seconds)
        )

    def _last_close(self, series_id: MarketSeriesId) -> int | None:
        candles = self._candles(series_id)
        return candles[-1].close_time_ms if candles else None

    @staticmethod
    def _continuity_valid(
        candles: tuple[ClosedCandle, ...],
        *,
        required: int,
        horizon_ms: int,
    ) -> bool:
        if len(candles) < required or not candles or candles[-1].close_time_ms != horizon_ms:
            return False
        relevant = candles[-required:]
        timeframe_ms = relevant[0].timeframe_seconds * 1_000
        return all(
            current.open_time_ms == previous.close_time_ms
            and current.close_time_ms - previous.close_time_ms == timeframe_ms
            for previous, current in zip(relevant, relevant[1:], strict=False)
        )
