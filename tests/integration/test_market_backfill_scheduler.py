from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from packages.brokers.deriv import (
    DerivCandleAdapter,
    DerivCandleHistoryPump,
    DerivCandleIngressBridge,
)
from packages.domain.market import MarketCandle, MarketHistoryBatch
from packages.domain.models import Broker
from packages.market_data import CandleIngress
from packages.market_pipeline import (
    BackfillPlanner,
    MarketBackfillCoordinator,
    MarketBackfillScheduler,
    MarketHealthGate,
    MarketPipelineMetrics,
    MarketSeriesHealth,
    MarketSeriesId,
    ReadOnlyBackfillRetryPolicy,
    TrustedClosedHorizon,
)
from packages.persistence.candle_repository import SqliteCandleRepository
from packages.persistence.strategy_data import StrategyDataDatabase

BASE_EPOCH = 1_800_000_000
TIMEFRAME = 60


@dataclass
class FakeClock:
    value: float = 0

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def series() -> MarketSeriesId:
    return MarketSeriesId(
        Broker.DERIV,
        "frxEURUSD",
        "EURUSD",
        "DIGITAL_OPTION",
        TIMEFRAME,
    )


def market_candle(index: int, *, closed: bool = True) -> MarketCandle:
    opened = datetime.fromtimestamp(BASE_EPOCH + index * TIMEFRAME, UTC)
    close = Decimal("101.000") if index % 2 == 0 else Decimal("99.000")
    return MarketCandle(
        broker=Broker.DERIV,
        broker_symbol="frxEURUSD",
        timeframe_seconds=TIMEFRAME,
        open_time=opened,
        close_time=opened + timedelta(seconds=TIMEFRAME),
        open=Decimal("100.000"),
        high=Decimal("102.000"),
        low=Decimal("98.000"),
        close=close,
        is_closed=closed,
    )


class WindowedHistorySource:
    def __init__(self, candles: tuple[MarketCandle, ...]) -> None:
        if not candles:
            raise ValueError("windowed history source requires candles")
        self.candles = candles
        self.symbol = candles[0].broker_symbol
        self.calls: list[tuple[int, int | None]] = []
        self.failures_remaining = 0
        self.omit_close_epoch_once: int | None = None
        self.overflow_once = False
        self.partial_once = False
        self.on_call: object | None = None
        self.trading_write_requests = 0
        self.deriv_order_submit_count = 0
        self.deriv_buy_count = 0

    def market_history_batch(
        self,
        symbol: str,
        *,
        style: str,
        count: int = 100,
        timeframe_seconds: int | None = None,
        end_epoch: int | None = None,
    ) -> MarketHistoryBatch:
        assert symbol == self.symbol
        assert style == "candles"
        assert timeframe_seconds == TIMEFRAME
        self.calls.append((count, end_epoch))
        if callable(self.on_call):
            self.on_call()
            self.on_call = None
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise TimeoutError("FAKE_HISTORY_TIMEOUT")
        eligible = tuple(
            candle
            for candle in self.candles
            if end_epoch is None or int(candle.close_time.timestamp()) <= end_epoch
        )
        selected = eligible[-count:]
        if self.omit_close_epoch_once is not None:
            missing = self.omit_close_epoch_once
            selected = tuple(
                candle for candle in selected if int(candle.close_time.timestamp()) != missing
            )
            self.omit_close_epoch_once = None
        if self.partial_once and selected:
            selected = (
                *selected[:-1],
                market_candle(self.candles.index(selected[-1]), closed=False),
            )
            self.partial_once = False
        if self.overflow_once and selected:
            selected = (*selected, selected[-1])
            self.overflow_once = False
        call = len(self.calls)
        return MarketHistoryBatch(
            response_message_id=f"history-response-{call}",
            correlation_id=f"history-correlation-{call}",
            causation_id=f"history-request-{call}",
            ticks=(),
            candles=selected,
        )


def build_stack(
    path: Path,
    source: WindowedHistorySource,
    *,
    required: int,
    max_batch: int = 100,
    max_pages: int = 32,
) -> tuple[
    StrategyDataDatabase,
    SqliteCandleRepository,
    MarketHealthGate,
    MarketBackfillCoordinator,
    MarketPipelineMetrics,
]:
    database = StrategyDataDatabase(path)
    repository = SqliteCandleRepository(database)
    health = MarketHealthGate()
    metrics = MarketPipelineMetrics()
    pump = DerivCandleHistoryPump(
        source,
        DerivCandleIngressBridge(
            DerivCandleAdapter(frozenset({"frxEURUSD"})),
            CandleIngress(repository),
        ),
        max_batch_size=max_batch,
        now=lambda: datetime.fromtimestamp(BASE_EPOCH + 10_000 * TIMEFRAME, UTC),
    )
    horizon = TrustedClosedHorizon(
        source_epoch_seconds=BASE_EPOCH + len(source.candles) * TIMEFRAME,
        close_epoch_ms=(BASE_EPOCH + len(source.candles) * TIMEFRAME) * 1_000,
        observed_monotonic=0,
    )
    coordinator = MarketBackfillCoordinator(
        repository,
        BackfillPlanner(
            max_candles_per_batch=max_batch,
            backfill_overlap_candles=2,
        ),
        pump,
        health,
        lambda _series: horizon,
        max_pages_per_recovery=max_pages,
        metrics=metrics,
    )
    coordinator.register(series(), required_closed_candles=required)
    return database, repository, health, coordinator, metrics


def test_bf01_bf02_bf03_initial_warmup_overlap_and_duplicates(tmp_path: Path) -> None:
    source = WindowedHistorySource(tuple(market_candle(index) for index in range(500)))
    database, repository, health, coordinator, metrics = build_stack(
        tmp_path / "strategy_data.db",
        source,
        required=500,
    )
    try:
        result = coordinator.recover(series(), 0)
        assert result.success
        assert len(source.calls) == 6
        assert all(count <= 100 for count, _ in source.calls)
        assert metrics.backfill_duplicates == 10
        assert len(repository.range((Broker.DERIV, "frxEURUSD", TIMEFRAME))) == 500
        assert health.snapshot(series()).health is MarketSeriesHealth.HEALTHY
    finally:
        database.close()


def test_bf04_bf05_gap_recovery_is_multi_batch_and_fail_closed(tmp_path: Path) -> None:
    source = WindowedHistorySource(tuple(market_candle(index) for index in range(220)))
    source.omit_close_epoch_once = BASE_EPOCH + 20 * TIMEFRAME
    database, repository, health, coordinator, metrics = build_stack(
        tmp_path / "strategy_data.db",
        source,
        required=220,
        max_batch=40,
    )
    try:
        first = coordinator.recover(series(), 0)
        assert not first.success
        assert health.snapshot(series()).health is MarketSeriesHealth.GAPPED
        assert not health.snapshot(series()).dispatch_allowed
        second = coordinator.recover(series(), 0)
        assert second.success
        persisted = repository.range((Broker.DERIV, "frxEURUSD", TIMEFRAME))
        assert len(persisted) == 220
        assert health.snapshot(series()).gap_count == 0
        assert metrics.gap_count == 1
        assert metrics.gap_recovery_count == 1
        assert metrics.backfill_requests == 7
        assert metrics.backfill_duplicates == 12
        assert metrics.shadow_candles_dispatched == 0
        assert metrics.shadow_decisions == 0
    finally:
        database.close()


def test_bf06_timeout_retries_and_bf07_exhaustion_blocks(tmp_path: Path) -> None:
    source = WindowedHistorySource(tuple(market_candle(index) for index in range(5)))
    source.failures_remaining = 1
    database, _, health, coordinator, _ = build_stack(tmp_path / "retry.db", source, required=5)
    clock = FakeClock()
    scheduler = MarketBackfillScheduler(
        clock,
        health,
        coordinator,
        retry_policy=ReadOnlyBackfillRetryPolicy(
            maximum_attempts=2,
            initial_delay_seconds=1,
            maximum_delay_seconds=1,
            jitter_ratio=0,
        ),
    )
    scheduler.register(series())
    try:
        scheduler.tick()
        assert scheduler.metrics.backfill_retries == 1
        clock.advance(1)
        scheduler.tick()
        assert health.snapshot(series()).health is MarketSeriesHealth.HEALTHY
    finally:
        database.close()

    exhausted = WindowedHistorySource(tuple(market_candle(index) for index in range(5)))
    exhausted.failures_remaining = 2
    second_db, _, second_health, second_coordinator, _ = build_stack(
        tmp_path / "exhausted.db", exhausted, required=5
    )
    second_clock = FakeClock()
    second_scheduler = MarketBackfillScheduler(
        second_clock,
        second_health,
        second_coordinator,
        retry_policy=ReadOnlyBackfillRetryPolicy(
            maximum_attempts=2,
            initial_delay_seconds=1,
            maximum_delay_seconds=1,
            jitter_ratio=0,
        ),
    )
    second_scheduler.register(series())
    try:
        second_scheduler.tick()
        second_clock.advance(1)
        second_scheduler.tick()
        assert second_health.snapshot(series()).health is MarketSeriesHealth.FAILED
        assert not second_health.snapshot(series()).dispatch_allowed
    finally:
        second_db.close()


def test_bf08_bf09_reconnect_generation_rejects_stale_response(tmp_path: Path) -> None:
    source = WindowedHistorySource(tuple(market_candle(index) for index in range(5)))
    database, _, health, coordinator, metrics = build_stack(
        tmp_path / "strategy_data.db", source, required=5
    )
    first_generation = health.start_reconnect(series())
    source.on_call = lambda: health.start_reconnect(series())
    try:
        stale = coordinator.recover(series(), first_generation)
        assert not stale.success
        assert health.snapshot(series()).health is MarketSeriesHealth.RECONNECTING
        current = health.snapshot(series()).reconnect_generation
        completed = coordinator.recover(series(), current)
        assert completed.success
        assert health.snapshot(series()).health is MarketSeriesHealth.HEALTHY
        assert source.calls[-1] == (2, BASE_EPOCH + 5 * TIMEFRAME)
        assert metrics.backfill_duplicates == 2
        assert metrics.reconnect_count == 1
    finally:
        database.close()


def test_bf10_worker_restart_reuses_durable_boundary(tmp_path: Path) -> None:
    candles = tuple(market_candle(index) for index in range(30))
    path = tmp_path / "strategy_data.db"
    first_source = WindowedHistorySource(candles)
    first_db, _, _, first_coordinator, _ = build_stack(path, first_source, required=30)
    assert first_coordinator.recover(series(), 0).success
    first_db.close()

    restarted_source = WindowedHistorySource(candles)
    second_db, repository, health, restarted, _ = build_stack(path, restarted_source, required=30)
    try:
        assert restarted.recover(series(), 0).success
        assert restarted_source.calls == [(2, BASE_EPOCH + 30 * TIMEFRAME)]
        assert len(repository.range((Broker.DERIV, "frxEURUSD", TIMEFRAME))) == 30
        assert health.snapshot(series()).health is MarketSeriesHealth.HEALTHY
    finally:
        second_db.close()


def test_bp01_to_bp03_overflow_requires_backfill_and_continuity(tmp_path: Path) -> None:
    source = WindowedHistorySource(tuple(market_candle(index) for index in range(10)))
    source.overflow_once = True
    database, _, health, coordinator, metrics = build_stack(
        tmp_path / "strategy_data.db", source, required=10
    )
    try:
        with pytest.raises(Exception, match="DERIV_CANDLE_BATCH_OVERFLOW"):
            coordinator.recover(series(), 0)
        assert health.snapshot(series()).health is MarketSeriesHealth.BACKPRESSURED
        assert not health.snapshot(series()).dispatch_allowed
        assert metrics.shadow_decisions == 0
        assert coordinator.recover(series(), 0).success
        assert health.snapshot(series()).health is MarketSeriesHealth.HEALTHY
        assert metrics.backfill_requests == 2
        assert metrics.shadow_decisions == 0
    finally:
        database.close()


def test_pc01_pc02_partial_candle_is_never_persisted_or_dispatched(tmp_path: Path) -> None:
    source = WindowedHistorySource(tuple(market_candle(index) for index in range(10)))
    source.partial_once = True
    database, repository, health, coordinator, metrics = build_stack(
        tmp_path / "strategy_data.db",
        source,
        required=10,
        max_pages=1,
    )
    try:
        result = coordinator.recover(series(), 0)
        assert not result.success
        assert len(repository.range((Broker.DERIV, "frxEURUSD", TIMEFRAME))) == 9
        assert metrics.partial_candles_received == 1
        assert metrics.partial_candles_persisted == 0
        assert metrics.partial_candles_dispatched == 0
        assert metrics.strategy_decisions_from_partial == 0
        assert not health.snapshot(series()).dispatch_allowed
    finally:
        database.close()
