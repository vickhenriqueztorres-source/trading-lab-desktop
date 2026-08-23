from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from packages.brokers.deriv import (
    DerivCandleAdapter,
    DerivCandleHistoryPump,
    DerivCandleIngressBridge,
)
from packages.domain.market import MarketCandle, MarketTick
from packages.domain.models import Broker
from packages.market_data import CandleIngress, ClosedCandle
from packages.market_pipeline import (
    AcceptedCandleDispatcher,
    BackfillJobResult,
    BackfillPlanner,
    ClosedCandleAggregator,
    ContinuousShadowRuntime,
    MarketBackfillCoordinator,
    MarketBackfillScheduler,
    MarketHealthGate,
    MarketHealthReason,
    MarketPipelineMetrics,
    MarketSeriesHealth,
    MarketSeriesId,
    ReplaySessionDecisionPipeline,
    ShadowDecisionFingerprint,
    TrustedClosedHorizon,
)
from packages.market_pipeline.dispatcher import ReplaySessionPort
from packages.persistence.candle_repository import SqliteCandleRepository
from packages.persistence.strategy_data import StrategyDataDatabase
from packages.replay import ReplayEngine, ReplayRequest
from tests.integration.test_market_backfill_scheduler import WindowedHistorySource
from tests.replay.test_recoverable_replay import (
    catalog_factory,
    persistence_for,
    recoverable_request,
)

BASE = 1_800_000_000
TIMEFRAME = 60


@dataclass
class FakeClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeLiveTickSource:
    def __init__(self, ticks: tuple[MarketTick, ...]) -> None:
        self._ticks = deque(ticks)
        self.subscribe_count = 0
        self.unsubscribe_count = 0
        self.trading_write_requests = 0
        self.deriv_order_submit_count = 0

    def subscribe_market_ticks(self, symbol: str) -> MarketTick:
        assert symbol == "EURUSD"
        self.subscribe_count += 1
        return self._ticks.popleft()

    def receive_market_tick(self, timeout: float) -> MarketTick | None:
        assert timeout > 0
        return self._ticks.popleft() if self._ticks else None

    def unsubscribe_market_ticks(self, subscription_id: str) -> bool:
        assert subscription_id
        self.unsubscribe_count += 1
        return True


class FailingReceiveTickSource(FakeLiveTickSource):
    def receive_market_tick(self, timeout: float) -> MarketTick | None:
        raise ConnectionError("simulated live disconnect")


class ImmediateHealthyBackfill:
    def __init__(self, health: MarketHealthGate) -> None:
        self._health = health
        self.generations: list[int] = []

    def recover(self, series_id: MarketSeriesId, generation: int) -> BackfillJobResult:
        self.generations.append(generation)
        success = self._health.complete_recovery(
            series_id,
            generation=generation,
            continuity_valid=True,
            clock_trusted=True,
            durable_closed_candles=1,
            last_durable_close=BASE * 1_000,
            last_source_event=f"recovery-{generation}",
        )
        return BackfillJobResult(
            generation=generation,
            success=success,
            last_durable_close_epoch=BASE,
            recovery_required=False,
            reason=MarketHealthReason.HEALTHY,
        )


class NoDecisionPipeline:
    def process_candle(self, candle: ClosedCandle, *, dispatch: bool) -> int:
        assert candle.close_time_ms > candle.open_time_ms
        assert not dispatch
        return 0


def aligned_candle(index: int) -> ClosedCandle:
    open_ms = (BASE + index * TIMEFRAME) * 1_000
    return ClosedCandle(
        broker=Broker.DERIV,
        symbol="EURUSD",
        timeframe_seconds=TIMEFRAME,
        open_time_ms=open_ms,
        close_time_ms=open_ms + TIMEFRAME * 1_000,
        open_units=100_000,
        high_units=102_000,
        low_units=98_000,
        close_units=101_000 if index % 2 == 0 else 99_000,
        price_scale=1_000,
        source="CONTINUOUS_FIXTURE",
        source_event_id=f"continuous-{index}",
        source_timestamp_ms=open_ms + TIMEFRAME * 1_000,
        received_timestamp_ms=open_ms + TIMEFRAME * 1_000,
    )


def aligned_request() -> ReplayRequest:
    return replace(
        recoverable_request(),
        candles=tuple(aligned_candle(index) for index in range(500)),
    )


def market_from_closed(candle: ClosedCandle) -> MarketCandle:
    quantum = Decimal(1) / Decimal(candle.price_scale)
    prices = tuple(
        (Decimal(value) / candle.price_scale).quantize(quantum) for value in candle.price_units
    )
    return MarketCandle(
        broker=candle.broker,
        broker_symbol=candle.symbol,
        timeframe_seconds=candle.timeframe_seconds,
        open_time=datetime.fromtimestamp(candle.open_time_ms // 1_000, UTC),
        close_time=datetime.fromtimestamp(candle.close_time_ms // 1_000, UTC),
        open=prices[0],
        high=prices[1],
        low=prices[2],
        close=prices[3],
        is_closed=True,
    )


def tick_for(
    epoch: int,
    quote: str,
    *,
    subscription_id: str = "live-sub-1",
) -> MarketTick:
    return MarketTick(
        broker=Broker.DERIV,
        broker_symbol="EURUSD",
        epoch=epoch,
        quote=Decimal(quote),
        received_at=datetime.fromtimestamp(epoch, UTC),
        subscription_id=subscription_id,
        source="FAKE_CONTINUOUS_LIVE",
    )


def live_ticks(start_index: int, end_index: int) -> tuple[MarketTick, ...]:
    ticks: list[MarketTick] = []
    for index in range(start_index, end_index):
        opened = BASE + index * TIMEFRAME
        ticks.extend(
            (
                tick_for(opened, "100.000"),
                tick_for(opened + 10, "102.000"),
                tick_for(opened + 20, "98.000"),
                tick_for(opened + 59, "101.000" if index % 2 == 0 else "99.000"),
            )
        )
    ticks.append(tick_for(BASE + end_index * TIMEFRAME, "100.000"))
    return tuple(ticks)


def fingerprint(session: ReplaySessionPort) -> ShadowDecisionFingerprint:
    result = session.result()
    return ShadowDecisionFingerprint(
        result.final_hash,
        len(result.signal_ids),
        len(result.risk_decisions),
    )


def reference_fingerprints(request: ReplayRequest) -> dict[int, ShadowDecisionFingerprint]:
    session = ReplayEngine(catalog_factory).create_session(request)
    references: dict[int, ShadowDecisionFingerprint] = {}
    for candle in request.candles:
        session.process(candle, dispatch=False)
        references[candle.close_time_ms] = fingerprint(session)
    return references


def test_continuous_shadow_history_live_replay_equivalence_and_metrics(tmp_path: Path) -> None:
    request = aligned_request()
    history = WindowedHistorySource(
        tuple(market_from_closed(candle) for candle in request.candles[:400])
    )
    live_source = FakeLiveTickSource(live_ticks(400, 500))
    identity = MarketSeriesId(
        Broker.DERIV,
        request.symbol,
        request.symbol,
        request.product,
        request.timeframe_seconds,
    )
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repository = SqliteCandleRepository(database)
    persistence = persistence_for(database)
    health = MarketHealthGate()
    metrics = MarketPipelineMetrics()
    session = ReplayEngine(catalog_factory).create_session(request, persistence=persistence)
    decision_pipeline = ReplaySessionDecisionPipeline(session)
    dispatcher = AcceptedCandleDispatcher(
        repository,
        health,
        decision_pipeline,
        metrics=metrics,
    )
    pump = DerivCandleHistoryPump(
        history,
        DerivCandleIngressBridge(
            DerivCandleAdapter(frozenset({request.symbol})),
            CandleIngress(repository),
        ),
        max_batch_size=100,
        now=lambda: datetime.fromtimestamp(BASE + 600 * TIMEFRAME, UTC),
    )
    horizon = TrustedClosedHorizon(
        source_epoch_seconds=request.candles[399].close_time_ms // 1_000,
        close_epoch_ms=request.candles[399].close_time_ms,
        observed_monotonic=0,
    )
    coordinator = MarketBackfillCoordinator(
        repository,
        BackfillPlanner(max_candles_per_batch=100, backfill_overlap_candles=2),
        pump,
        health,
        lambda _series: horizon,
        dispatcher=dispatcher,
        dispatch_cursor=lambda _series: (
            checkpoint.last_close_time_ms
            if (checkpoint := persistence.warmup.latest(request.context)) is not None
            else None
        ),
        metrics=metrics,
    )
    coordinator.register(identity, required_closed_candles=400)
    clock = FakeClock()
    scheduler = MarketBackfillScheduler(clock, health, coordinator, metrics=metrics)
    scheduler.register(identity)
    references = reference_fingerprints(request)
    runtime = ContinuousShadowRuntime(
        identity,
        live_source,
        ClosedCandleAggregator(identity, price_scale=1_000, max_seen_ticks=512),
        CandleIngress(repository),
        health,
        scheduler,
        dispatcher,
        clock,
        fingerprint=decision_pipeline.fingerprint,
        reference=lambda close_time_ms: references.get(close_time_ms),
        metrics=metrics,
    )
    try:
        assert runtime.start()
        assert live_source.subscribe_count == 1
        for _ in range(400):
            runtime.poll_once(timeout=0.01)
        shadow = session.complete()
        clean = ReplayEngine(catalog_factory).run(request)
        assert shadow == clean
        assert shadow.final_hash == clean.final_hash
        assert metrics.shadow_candles_dispatched == 500
        assert metrics.live_candles_closed == 100
        assert metrics.live_ticks_received == 401
        assert metrics.live_replay_comparisons == 100
        assert metrics.live_replay_divergences == 0
        assert metrics.live_dispatch_lag_ms_max == 0
        assert metrics.subscription_restores == 1
        assert live_source.trading_write_requests == 0
        assert live_source.deriv_order_submit_count == 0
        assert not (tmp_path / "state.db").exists()
    finally:
        runtime.stop()
        database.close()


def test_disconnect_requires_new_generation_backfill_before_subscription_restore(
    tmp_path: Path,
) -> None:
    identity = MarketSeriesId(Broker.DERIV, "EURUSD", "EURUSD", "OPTION", TIMEFRAME)
    health = MarketHealthGate()
    health.register(identity, required_closed_candles=1)
    job = ImmediateHealthyBackfill(health)
    clock = FakeClock()
    scheduler = MarketBackfillScheduler(clock, health, job)
    scheduler.register(identity)
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repository = SqliteCandleRepository(database)
    source = FakeLiveTickSource(
        (
            tick_for(BASE, "100.000", subscription_id="sub-generation-0"),
            tick_for(BASE + TIMEFRAME, "101.000", subscription_id="sub-generation-1"),
        )
    )
    runtime = ContinuousShadowRuntime(
        identity,
        source,
        ClosedCandleAggregator(identity, price_scale=1_000),
        CandleIngress(repository),
        health,
        scheduler,
        AcceptedCandleDispatcher(repository, health, NoDecisionPipeline()),
        clock,
    )
    try:
        assert runtime.start()
        assert job.generations == [0]
        assert source.subscribe_count == 1
        assert runtime.on_disconnect() == 1
        assert not runtime.subscribed
        assert health.snapshot(identity).health is MarketSeriesHealth.RECONNECTING
        assert runtime.poll_once(timeout=0.01) is None
        assert source.subscribe_count == 1
        assert runtime.recover_and_restore()
        assert job.generations == [0, 1]
        assert source.subscribe_count == 2
        assert health.snapshot(identity).health is MarketSeriesHealth.HEALTHY
    finally:
        runtime.stop()
        database.close()


def test_live_replay_divergence_fails_market_health_closed(tmp_path: Path) -> None:
    identity = MarketSeriesId(Broker.DERIV, "EURUSD", "EURUSD", "OPTION", TIMEFRAME)
    health = MarketHealthGate()
    health.register(identity, required_closed_candles=1)
    job = ImmediateHealthyBackfill(health)
    clock = FakeClock()
    scheduler = MarketBackfillScheduler(clock, health, job)
    scheduler.register(identity)
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repository = SqliteCandleRepository(database)
    source = FakeLiveTickSource(
        (
            tick_for(BASE, "100.000"),
            tick_for(BASE + 10, "102.000"),
            tick_for(BASE + 20, "98.000"),
            tick_for(BASE + 59, "101.000"),
            tick_for(BASE + TIMEFRAME, "100.000"),
        )
    )
    metrics = MarketPipelineMetrics()
    dispatcher = AcceptedCandleDispatcher(
        repository,
        health,
        NoDecisionPipeline(),
        metrics=metrics,
    )
    runtime = ContinuousShadowRuntime(
        identity,
        source,
        ClosedCandleAggregator(identity, price_scale=1_000),
        CandleIngress(repository),
        health,
        scheduler,
        dispatcher,
        clock,
        fingerprint=lambda: ShadowDecisionFingerprint("a" * 64, 0, 0),
        reference=lambda _close: ShadowDecisionFingerprint("b" * 64, 0, 0),
        metrics=metrics,
    )
    try:
        assert runtime.start()
        for _ in range(4):
            runtime.poll_once(timeout=0.01)
        snapshot = health.snapshot(identity)
        assert snapshot.health is MarketSeriesHealth.FAILED
        assert snapshot.reason is MarketHealthReason.SHADOW_DIVERGENCE
        assert not snapshot.dispatch_allowed
        assert metrics.live_replay_comparisons == 1
        assert metrics.live_replay_divergences == 1
        assert runtime.poll_once(timeout=0.01) is None
    finally:
        runtime.stop()
        database.close()


def test_receive_failure_marks_reconnecting_before_propagating(tmp_path: Path) -> None:
    identity = MarketSeriesId(Broker.DERIV, "EURUSD", "EURUSD", "OPTION", TIMEFRAME)
    health = MarketHealthGate()
    health.register(identity, required_closed_candles=1)
    job = ImmediateHealthyBackfill(health)
    clock = FakeClock()
    scheduler = MarketBackfillScheduler(clock, health, job)
    scheduler.register(identity)
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repository = SqliteCandleRepository(database)
    source = FailingReceiveTickSource((tick_for(BASE, "100.000"),))
    metrics = MarketPipelineMetrics()
    runtime = ContinuousShadowRuntime(
        identity,
        source,
        ClosedCandleAggregator(identity, price_scale=1_000),
        CandleIngress(repository),
        health,
        scheduler,
        AcceptedCandleDispatcher(repository, health, NoDecisionPipeline()),
        clock,
        metrics=metrics,
    )
    try:
        assert runtime.start()
        with pytest.raises(ConnectionError, match="simulated live disconnect"):
            runtime.poll_once(timeout=0.01)
        snapshot = health.snapshot(identity)
        assert snapshot.health is MarketSeriesHealth.RECONNECTING
        assert not snapshot.dispatch_allowed
        assert not runtime.subscribed
        assert metrics.reconnect_count == 1
    finally:
        runtime.stop()
        database.close()


def test_stale_timeout_forgets_subscription_and_discards_partial_bucket(
    tmp_path: Path,
) -> None:
    identity = MarketSeriesId(Broker.DERIV, "EURUSD", "EURUSD", "OPTION", TIMEFRAME)
    health = MarketHealthGate()
    health.register(identity, required_closed_candles=1)
    job = ImmediateHealthyBackfill(health)
    clock = FakeClock()
    scheduler = MarketBackfillScheduler(clock, health, job)
    scheduler.register(identity)
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repository = SqliteCandleRepository(database)
    source = FakeLiveTickSource((tick_for(BASE, "100.000"),))
    aggregator = ClosedCandleAggregator(identity, price_scale=1_000)
    runtime = ContinuousShadowRuntime(
        identity,
        source,
        aggregator,
        CandleIngress(repository),
        health,
        scheduler,
        AcceptedCandleDispatcher(repository, health, NoDecisionPipeline()),
        clock,
        stale_after_seconds=5,
    )
    try:
        assert runtime.start()
        assert aggregator.seen_tick_count == 1
        clock.advance(6)
        assert runtime.poll_once(timeout=0.01) is None
        snapshot = health.snapshot(identity)
        assert snapshot.health is MarketSeriesHealth.STALE
        assert not snapshot.dispatch_allowed
        assert not runtime.subscribed
        assert source.unsubscribe_count == 1
        assert aggregator.seen_tick_count == 0
    finally:
        runtime.stop()
        database.close()
