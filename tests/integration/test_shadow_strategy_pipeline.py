from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from packages.brokers.deriv import (
    DerivCandleAdapter,
    DerivCandleHistoryPump,
    DerivCandleIngressBridge,
)
from packages.domain.market import MarketCandle
from packages.domain.models import Broker
from packages.market_data import CandleIngress, ClosedCandle
from packages.market_pipeline import (
    AcceptedCandleDispatcher,
    BackfillPlanner,
    ExecutionCapabilityError,
    ExecutionCapabilityGate,
    ExecutionMode,
    MarketBackfillCoordinator,
    MarketHealthGate,
    MarketPipelineMetrics,
    MarketSeriesId,
    ReplaySessionDecisionPipeline,
    TrustedClosedHorizon,
)
from packages.persistence.candle_repository import SqliteCandleRepository
from packages.persistence.strategy_data import StrategyDataDatabase
from packages.replay import ReplayEngine
from tests.integration.test_market_backfill_scheduler import (
    BASE_EPOCH,
    TIMEFRAME,
    WindowedHistorySource,
    series,
)
from tests.replay.test_recoverable_replay import (
    catalog_factory,
    persistence_for,
    recoverable_request,
)


def market_from_closed(candle: ClosedCandle) -> MarketCandle:
    scale = Decimal(candle.price_scale)
    quantum = Decimal(1) / scale
    prices = tuple((Decimal(value) / scale).quantize(quantum) for value in candle.price_units)
    return MarketCandle(
        broker=candle.broker,
        broker_symbol=candle.symbol,
        timeframe_seconds=candle.timeframe_seconds,
        open_time=datetime.fromtimestamp(candle.open_time_ms / 1_000, UTC),
        close_time=datetime.fromtimestamp(candle.close_time_ms / 1_000, UTC),
        open=prices[0],
        high=prices[1],
        low=prices[2],
        close=prices[3],
        is_closed=True,
    )


def test_shadow_01_to_06_dispatch_false_zero_financial_state_and_equivalence(
    tmp_path: Path,
) -> None:
    request = recoverable_request()
    source = WindowedHistorySource(tuple(market_from_closed(candle) for candle in request.candles))
    identity = MarketSeriesId(
        Broker.DERIV,
        request.symbol,
        request.symbol,
        request.product,
        request.timeframe_seconds,
    )
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repository = SqliteCandleRepository(database)
    health = MarketHealthGate()
    metrics = MarketPipelineMetrics()
    pump = DerivCandleHistoryPump(
        source,
        DerivCandleIngressBridge(
            DerivCandleAdapter(frozenset({request.symbol})),
            CandleIngress(repository),
        ),
        max_batch_size=100,
        now=lambda: datetime.fromtimestamp(request.candles[-1].close_time_ms / 1_000 + 60, UTC),
    )
    horizon = TrustedClosedHorizon(
        source_epoch_seconds=request.candles[-1].close_time_ms // 1_000,
        close_epoch_ms=request.candles[-1].close_time_ms,
        observed_monotonic=0,
    )
    ingress_only = MarketBackfillCoordinator(
        repository,
        BackfillPlanner(max_candles_per_batch=100, backfill_overlap_candles=2),
        pump,
        health,
        lambda _series: horizon,
        metrics=metrics,
    )
    ingress_only.register(identity, required_closed_candles=500)
    try:
        assert ingress_only.recover(identity, 0).success
        durable = repository.range((Broker.DERIV, request.symbol, TIMEFRAME))
        assert len(durable) == 500
        assert tuple(candle.candle_id for candle in durable) == tuple(
            candle.candle_id for candle in request.candles
        )
        clean = ReplayEngine(catalog_factory).run(request)

        persistence = persistence_for(database)
        session = ReplayEngine(catalog_factory).create_session(
            request,
            persistence=persistence,
        )
        dispatcher = AcceptedCandleDispatcher(
            repository,
            health,
            ReplaySessionDecisionPipeline(session),
            metrics=metrics,
        )
        delivery = MarketBackfillCoordinator(
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
        delivery.register(identity, required_closed_candles=500)
        assert delivery.recover(identity, 0).success
        shadow = session.complete()

        assert shadow == clean
        assert shadow.final_hash == clean.final_hash
        assert metrics.shadow_candles_dispatched == 500
        assert metrics.shadow_decisions == len(shadow.risk_decisions)
        assert len(shadow.risk_decisions) == 101
        assert not (tmp_path / "state.db").exists()
        with sqlite3.connect(database.path) as connection:
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        assert "outbox" not in tables
        assert "trade_intents" not in tables
        assert "risk_reservations" not in tables
        assert source.calls
        assert source.trading_write_requests == 0
        assert source.deriv_order_submit_count == 0
        assert source.deriv_buy_count == 0
    finally:
        database.close()


def test_shadow_persist_before_dispatch_and_health_block(tmp_path: Path) -> None:
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repository = SqliteCandleRepository(database)
    health = MarketHealthGate()
    identity = series()
    health.register(identity, required_closed_candles=1)
    candle = ClosedCandle(
        broker=Broker.DERIV,
        symbol="frxEURUSD",
        timeframe_seconds=TIMEFRAME,
        open_time_ms=BASE_EPOCH * 1_000,
        close_time_ms=(BASE_EPOCH + TIMEFRAME) * 1_000,
        open_units=100_000,
        high_units=102_000,
        low_units=98_000,
        close_units=101_000,
        price_scale=1_000,
        source="TEST",
        source_event_id="accepted-1",
        source_timestamp_ms=(BASE_EPOCH + TIMEFRAME) * 1_000,
        received_timestamp_ms=(BASE_EPOCH + TIMEFRAME) * 1_000,
    )

    class ProbePipeline:
        calls = 0

        def process_candle(self, value: ClosedCandle, *, dispatch: bool) -> int:
            assert dispatch is False
            assert repository.get(value.candle_id) == value
            self.calls += 1
            return 0

    probe = ProbePipeline()
    dispatcher = AcceptedCandleDispatcher(repository, health, probe)
    try:
        with pytest.raises(RuntimeError, match="MARKET_HEALTH_BLOCKED"):
            dispatcher.dispatch(identity, candle)
        assert probe.calls == 0
        repository.store(candle)
        assert health.complete_recovery(
            identity,
            generation=0,
            continuity_valid=True,
            clock_trusted=True,
            durable_closed_candles=1,
            last_durable_close=candle.close_time_ms,
            last_source_event=candle.source_event_id,
        )
        dispatcher.dispatch(identity, candle)
        assert probe.calls == 1
    finally:
        database.close()


def test_shadow_capability_denies_dispatch_true_even_if_misconfigured() -> None:
    safe = ExecutionCapabilityGate()
    safe.ensure(dispatch=False)
    with pytest.raises(ExecutionCapabilityError, match="CAPABILITY_DENIED"):
        safe.ensure(dispatch=True)
    with pytest.raises(ExecutionCapabilityError, match="CAPABILITY_DENIED"):
        ExecutionCapabilityGate(
            can_submit_orders=True,
            mode=ExecutionMode.BROKER_EXECUTION,
        ).ensure(dispatch=False)


def test_shadow_license_authorization_cannot_elevate_execution_mode() -> None:
    license_allows_new_entries = True
    capability = ExecutionCapabilityGate()
    assert license_allows_new_entries
    assert capability.mode is ExecutionMode.DECISION_ONLY
    assert not capability.can_submit_orders
    with pytest.raises(ExecutionCapabilityError, match="CAPABILITY_DENIED"):
        capability.ensure(dispatch=license_allows_new_entries)
