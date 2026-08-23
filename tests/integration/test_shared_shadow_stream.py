from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from apps.core.broker_shadow_session import BrokerShadowSession
from apps.core.broker_shadow_soak import (
    BrokerShadowSoakLimits,
    BrokerShadowSoakRunner,
    BrokerShadowSoakState,
    PopenChildProcessProbe,
)
from apps.core.health import HealthGate
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSpec, ReadOnlyWorkerSupervisor
from apps.core.shadow_runtime import ReadOnlyMarketClient, ShadowServiceState
from apps.core.worker_supervisor import WorkerHealthState
from apps.deriv_worker.fake_transport import FakeDerivScenario
from packages.domain.market import MarketTick
from packages.domain.models import Broker
from packages.market_data import CandleIngress, ClosedCandle, InMemoryCandleStore
from packages.market_pipeline import (
    ClosedCandleAggregator,
    ContinuousShadowRuntime,
    LiveAggregationStatus,
    LiveTickSource,
    MarketHealthGate,
    MarketPipelineMetrics,
    MarketSeriesHealth,
    MarketSeriesId,
    SharedMarketTickRouter,
)
from packages.protocol.envelope import EndpointRole


@dataclass(slots=True)
class ManualClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value


class NoopScheduler:
    def tick(self) -> None:
        return

    def trigger(self, _series_id: MarketSeriesId) -> None:
        return


class AutoRecoveryScheduler:
    def __init__(self, health: MarketHealthGate, series_id: MarketSeriesId) -> None:
        self._health = health
        self._series = series_id
        self.triggers = 0

    def tick(self) -> None:
        snapshot = self._health.snapshot(self._series)
        if snapshot.dispatch_allowed:
            return
        self._health.complete_recovery(
            self._series,
            generation=snapshot.reconnect_generation,
            continuity_valid=True,
            clock_trusted=True,
            durable_closed_candles=1,
            last_durable_close=1_700_000_100_000,
            last_source_event="session-recovery-fixture",
        )

    def trigger(self, _series_id: MarketSeriesId) -> None:
        self.triggers += 1


class RecordingDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[tuple[MarketSeriesId, ClosedCandle]] = []

    def dispatch(self, series_id: MarketSeriesId, candle: ClosedCandle) -> None:
        self.dispatched.append((series_id, candle))


class FakeSharedTickSource:
    def __init__(self) -> None:
        self.events: queue.Queue[MarketTick] = queue.Queue()
        self._next_subscription = 0
        self.subscriptions: dict[str, str] = {}

    def subscribe_market_ticks(self, symbol: str) -> MarketTick:
        self._next_subscription += 1
        subscription_id = f"shared-sub-{self._next_subscription}"
        self.subscriptions[symbol] = subscription_id
        return market_tick(symbol, subscription_id, epoch=1_800_000_000)

    def receive_market_tick(self, timeout: float) -> MarketTick | None:
        try:
            return self.events.get(timeout=timeout)
        except queue.Empty:
            return None

    def unsubscribe_market_ticks(self, subscription_id: str) -> bool:
        for symbol, current in tuple(self.subscriptions.items()):
            if current == subscription_id:
                self.subscriptions.pop(symbol)
        return True


def market_series(symbol: str) -> MarketSeriesId:
    return MarketSeriesId(
        Broker.DERIV,
        symbol,
        symbol,
        "OPTION",
        60,
    )


def market_tick(symbol: str, subscription_id: str, *, epoch: int) -> MarketTick:
    return MarketTick(
        broker=Broker.DERIV,
        broker_symbol=symbol,
        epoch=epoch,
        quote=Decimal("1.08500"),
        received_at=datetime.fromtimestamp(epoch, UTC),
        subscription_id=subscription_id,
        source="FAKE_SHARED_DERIV",
    )


def test_two_shadow_runtimes_share_one_stream_without_competing_for_ticks() -> None:
    clock = ManualClock()
    source = FakeSharedTickSource()
    router = SharedMarketTickRouter(Broker.DERIV, source, per_series_queue_size=8)
    health = MarketHealthGate()
    store = InMemoryCandleStore(max_candles=16)
    ingress = CandleIngress(store)
    dispatcher = RecordingDispatcher()
    metrics = MarketPipelineMetrics()
    eurusd = market_series("frxEURUSD")
    gbpusd = market_series("frxGBPUSD")
    health.register(eurusd, required_closed_candles=1)
    health.register(gbpusd, required_closed_candles=1)
    for item in (eurusd, gbpusd):
        health.complete_recovery(
            item,
            generation=0,
            continuity_valid=True,
            clock_trusted=True,
            durable_closed_candles=1,
            last_durable_close=1_800_000_000_000,
            last_source_event="fixture",
        )
    eur_source = router.register(eurusd)
    gbp_source = router.register(gbpusd)
    eur_runtime = ContinuousShadowRuntime(
        eurusd,
        eur_source,
        ClosedCandleAggregator(eurusd, price_scale=100_000),
        ingress,
        health,
        NoopScheduler(),
        dispatcher,
        clock,
        metrics=metrics,
    )
    gbp_runtime = ContinuousShadowRuntime(
        gbpusd,
        gbp_source,
        ClosedCandleAggregator(gbpusd, price_scale=100_000),
        ingress,
        health,
        NoopScheduler(),
        dispatcher,
        clock,
        metrics=metrics,
    )

    assert eur_runtime.start()
    assert gbp_runtime.start()
    eur_subscription = source.subscriptions["frxEURUSD"]
    gbp_subscription = source.subscriptions["frxGBPUSD"]
    source.events.put(market_tick("frxGBPUSD", gbp_subscription, epoch=1_800_000_060))
    source.events.put(market_tick("frxEURUSD", eur_subscription, epoch=1_800_000_060))

    assert eur_runtime.poll_once(timeout=0.01) is None
    gbp_result = gbp_runtime.poll_once(timeout=0.01)
    assert gbp_result is not None
    assert gbp_result.status is LiveAggregationStatus.CLOSED
    eur_result = eur_runtime.poll_once(timeout=0.01)
    assert eur_result is not None
    assert eur_result.status is LiveAggregationStatus.CLOSED

    closed_by_symbol = {candle.symbol for _series_id, candle in dispatcher.dispatched}
    assert closed_by_symbol == {"frxEURUSD", "frxGBPUSD"}
    snapshot = router.snapshot()
    assert snapshot.registered_series == 2
    assert snapshot.active_subscriptions == 2
    assert snapshot.backpressure_count == 0
    assert metrics.live_candles_closed == 2


def test_two_shadow_runtimes_share_one_deriv_ipc_client() -> None:
    clock = ManualClock()
    worker = ReadOnlyWorkerSupervisor(
        HealthGate(),
        ReadOnlyWorkerSpec(
            module="apps.deriv_worker",
            role=EndpointRole.DERIV_WORKER,
            broker="DERIV",
            extra_arguments=("--scenario", FakeDerivScenario.SHADOW_CANDLES.value),
        ),
        heartbeat_interval=0.05,
        heartbeat_timeout=0.5,
        event_queue_size=256,
    )
    health = MarketHealthGate()
    store = InMemoryCandleStore(max_candles=16)
    ingress = CandleIngress(store)
    dispatcher = RecordingDispatcher()
    metrics = MarketPipelineMetrics()
    eurusd = market_series("frxEURUSD")
    gbpusd = market_series("frxGBPUSD")
    for item in (eurusd, gbpusd):
        health.register(item, required_closed_candles=1)
        health.complete_recovery(
            item,
            generation=0,
            continuity_valid=True,
            clock_trusted=True,
            durable_closed_candles=1,
            last_durable_close=1_700_000_100_000,
            last_source_event="ipc-fixture",
        )
    try:
        client = worker.start()
        first_process = worker.process
        assert first_process is not None
        router = SharedMarketTickRouter(Broker.DERIV, client, per_series_queue_size=128)
        eur_runtime = ContinuousShadowRuntime(
            eurusd,
            router.register(eurusd),
            ClosedCandleAggregator(eurusd, price_scale=100_000),
            ingress,
            health,
            NoopScheduler(),
            dispatcher,
            clock,
            metrics=metrics,
        )
        gbp_runtime = ContinuousShadowRuntime(
            gbpusd,
            router.register(gbpusd),
            ClosedCandleAggregator(gbpusd, price_scale=100_000),
            ingress,
            health,
            NoopScheduler(),
            dispatcher,
            clock,
            metrics=metrics,
        )
        assert eur_runtime.start()
        assert gbp_runtime.start()
        for _ in range(300):
            eur_runtime.poll_once(timeout=0.05)
            gbp_runtime.poll_once(timeout=0.05)
            if metrics.live_candles_closed == 2:
                break
        assert worker.process is first_process
        assert metrics.live_candles_closed == 2
        assert {candle.symbol for _series_id, candle in dispatcher.dispatched} == {
            "frxEURUSD",
            "frxGBPUSD",
        }
        snapshot = router.snapshot()
        assert snapshot.active_subscriptions == 2
        assert snapshot.backpressure_count == 0
    finally:
        worker.shutdown()


def test_broker_shadow_session_restarts_one_deriv_worker_for_two_series() -> None:
    clock = ManualClock()
    worker = ReadOnlyWorkerSupervisor(
        HealthGate(),
        ReadOnlyWorkerSpec(
            module="apps.deriv_worker",
            role=EndpointRole.DERIV_WORKER,
            broker="DERIV",
            extra_arguments=("--scenario", FakeDerivScenario.SHADOW_CANDLES.value),
        ),
        heartbeat_interval=0.05,
        heartbeat_timeout=0.5,
        event_queue_size=256,
    )
    health = MarketHealthGate()
    store = InMemoryCandleStore(max_candles=32)
    ingress = CandleIngress(store)
    dispatcher = RecordingDispatcher()
    metrics = MarketPipelineMetrics()
    eurusd = market_series("frxEURUSD")
    gbpusd = market_series("frxGBPUSD")
    for item in (eurusd, gbpusd):
        health.register(item, required_closed_candles=1)
        health.complete_recovery(
            item,
            generation=0,
            continuity_valid=True,
            clock_trusted=True,
            durable_closed_candles=1,
            last_durable_close=1_700_000_100_000,
            last_source_event="session-start-fixture",
        )

    session = BrokerShadowSession(Broker.DERIV, worker, clock=clock)

    def factory(
        _client: ReadOnlyMarketClient,
        source: LiveTickSource,
        series_id: MarketSeriesId,
    ) -> ContinuousShadowRuntime:
        return ContinuousShadowRuntime(
            series_id,
            source,
            ClosedCandleAggregator(series_id, price_scale=100_000),
            ingress,
            health,
            AutoRecoveryScheduler(health, series_id),
            dispatcher,
            clock,
            metrics=metrics,
        )

    session.register(eurusd, factory)
    session.register(gbpusd, factory)
    try:
        assert session.start()
        first_process = worker.process
        assert first_process is not None
        first_pid = first_process.pid
        assert session.snapshot().subscribed_series == 2

        for _ in range(360):
            session.poll_once(timeout=0.05)
            if metrics.live_candles_closed == 2:
                break
        assert metrics.live_candles_closed == 2

        first_process.kill()
        first_process.wait(timeout=2)
        deadline = time.monotonic() + 2
        while worker.health_state is WorkerHealthState.READY and time.monotonic() < deadline:
            time.sleep(0.01)
        assert session.poll_once(timeout=0.01) is None
        assert session.state is ShadowServiceState.RECOVERING
        assert health.snapshot(eurusd).health is MarketSeriesHealth.RECONNECTING
        assert health.snapshot(gbpusd).health is MarketSeriesHealth.RECONNECTING

        assert session.recover()
        second_process = worker.process
        assert second_process is not None
        assert second_process.pid != first_pid
        snapshot = session.snapshot()
        assert snapshot.state is ShadowServiceState.RUNNING
        assert snapshot.recovery_attempts == 1
        assert snapshot.subscribed_series == 2
        assert snapshot.router is not None
        assert snapshot.router.active_subscriptions == 2
        assert worker.process is second_process
    finally:
        session.shutdown()


def test_broker_shadow_soak_recovers_shared_deriv_worker_with_child_telemetry() -> None:
    clock = ManualClock()
    worker = ReadOnlyWorkerSupervisor(
        HealthGate(),
        ReadOnlyWorkerSpec(
            module="apps.deriv_worker",
            role=EndpointRole.DERIV_WORKER,
            broker="DERIV",
            extra_arguments=("--scenario", FakeDerivScenario.SHADOW_CANDLES.value),
        ),
        heartbeat_interval=0.05,
        heartbeat_timeout=0.5,
        event_queue_size=256,
    )
    health = MarketHealthGate()
    store = InMemoryCandleStore(max_candles=32)
    ingress = CandleIngress(store)
    dispatcher = RecordingDispatcher()
    metrics = MarketPipelineMetrics()
    eurusd = market_series("frxEURUSD")
    gbpusd = market_series("frxGBPUSD")
    for item in (eurusd, gbpusd):
        health.register(item, required_closed_candles=1)
        health.complete_recovery(
            item,
            generation=0,
            continuity_valid=True,
            clock_trusted=True,
            durable_closed_candles=1,
            last_durable_close=1_700_000_100_000,
            last_source_event="soak-start-fixture",
        )

    session = BrokerShadowSession(Broker.DERIV, worker, clock=clock)

    def factory(
        _client: ReadOnlyMarketClient,
        source: LiveTickSource,
        series_id: MarketSeriesId,
    ) -> ContinuousShadowRuntime:
        return ContinuousShadowRuntime(
            series_id,
            source,
            ClosedCandleAggregator(series_id, price_scale=100_000),
            ingress,
            health,
            AutoRecoveryScheduler(health, series_id),
            dispatcher,
            clock,
            metrics=metrics,
        )

    session.register(eurusd, factory)
    session.register(gbpusd, factory)
    killed_pid: list[int] = []

    def kill_worker_once(cycle: int, _session: BrokerShadowSession) -> None:
        if cycle != 4 or killed_pid:
            return
        process = worker.process
        assert process is not None
        killed_pid.append(process.pid)
        process.kill()
        process.wait(timeout=2)
        deadline = time.monotonic() + 2
        while worker.health_state is WorkerHealthState.READY and time.monotonic() < deadline:
            time.sleep(0.01)

    runner = BrokerShadowSoakRunner(
        session,
        clock=clock,
        limits=BrokerShadowSoakLimits(
            max_cycles=24,
            poll_timeout_seconds=0.02,
            max_recoveries=1,
            max_live_dispatch_lag_ms=5_000,
        ),
        child_process_probe=PopenChildProcessProbe(lambda: worker.process),
        before_cycle=kill_worker_once,
    )

    try:
        snapshot = runner.run_until_complete()

        assert snapshot.state is BrokerShadowSoakState.COMPLETED
        assert snapshot.recovery_attempts == 1
        assert killed_pid
        assert worker.process is not None
        assert worker.process.pid != killed_pid[0]
        assert snapshot.latest_resources is not None
        assert snapshot.latest_resources.child_pid == worker.process.pid
        assert snapshot.latest_resources.child_process_alive
        assert snapshot.session.subscribed_series == 2
        assert snapshot.session.router is not None
        assert snapshot.session.router.active_subscriptions == 2
        assert metrics.subscription_restores >= 4
    finally:
        runner.shutdown()
