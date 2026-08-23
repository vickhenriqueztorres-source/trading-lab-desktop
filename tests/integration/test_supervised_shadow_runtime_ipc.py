from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from apps.core.health import HealthGate
from apps.core.read_only_worker_supervisor import (
    ReadOnlyWorkerSpec,
    ReadOnlyWorkerSupervisor,
)
from apps.core.shadow_host import ShadowHostLimits, ShadowHostState, ShadowRuntimeHost
from apps.core.shadow_runtime import (
    ReadOnlyMarketClient,
    ShadowServiceState,
    SupervisedShadowRuntime,
)
from apps.core.worker_supervisor import RestartPolicy, WorkerHealthState
from apps.deriv_worker.fake_transport import FakeDerivScenario
from packages.brokers.deriv import (
    DerivCandleAdapter,
    DerivCandleHistoryPump,
    DerivCandleIngressBridge,
)
from packages.domain.models import Broker
from packages.market_data import CandleIngress, ClosedCandle
from packages.market_pipeline import (
    AcceptedCandleDispatcher,
    BackfillPlanner,
    ClosedCandleAggregator,
    ContinuousShadowRuntime,
    MarketBackfillCoordinator,
    MarketBackfillScheduler,
    MarketHealthGate,
    MarketPipelineMetrics,
    MarketSeriesHealth,
    MarketSeriesId,
    SystemMonotonicClock,
    trusted_closed_horizon,
)
from packages.persistence.candle_repository import SqliteCandleRepository
from packages.persistence.strategy_data import StrategyDataDatabase
from packages.protocol.envelope import EndpointRole


class RecordingDecisionOnlyPipeline:
    def __init__(self) -> None:
        self.close_times: list[int] = []

    @property
    def cursor(self) -> int | None:
        return self.close_times[-1] if self.close_times else None

    def process_candle(self, candle: ClosedCandle, *, dispatch: bool) -> int:
        assert not dispatch
        self.close_times.append(candle.close_time_ms)
        return 0


@dataclass
class HostClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_supervised_shadow_real_ipc_kill_backfill_restore_and_live_candle(
    tmp_path: Path,
) -> None:
    clock = SystemMonotonicClock()
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
    )
    database = StrategyDataDatabase(tmp_path / "strategy_data.db")
    repository = SqliteCandleRepository(database)
    ingress = CandleIngress(repository)
    health = MarketHealthGate()
    metrics = MarketPipelineMetrics()
    identity = MarketSeriesId(
        Broker.DERIV,
        "frxEURUSD",
        "EURUSD",
        "OPTION",
        60,
    )
    pipeline = RecordingDecisionOnlyPipeline()
    dispatcher = AcceptedCandleDispatcher(
        repository,
        health,
        pipeline,
        metrics=metrics,
    )

    def runtime_factory(client: ReadOnlyMarketClient) -> ContinuousShadowRuntime:
        pump = DerivCandleHistoryPump(
            client,
            DerivCandleIngressBridge(
                DerivCandleAdapter(frozenset({identity.broker_symbol})),
                ingress,
            ),
            max_batch_size=16,
            now=lambda: datetime.fromtimestamp(1_700_000_100, UTC),
        )

        def horizon(_series: MarketSeriesId):
            source_clock = client.broker_clock()
            return trusted_closed_horizon(
                identity,
                source_epoch_seconds=source_clock.server_epoch,
                observed_monotonic=clock.now(),
            )

        coordinator = MarketBackfillCoordinator(
            repository,
            BackfillPlanner(max_candles_per_batch=16, backfill_overlap_candles=1),
            pump,
            health,
            horizon,
            dispatcher=dispatcher,
            dispatch_cursor=lambda _series: pipeline.cursor,
            metrics=metrics,
        )
        coordinator.register(identity, required_closed_candles=1)
        scheduler = MarketBackfillScheduler(clock, health, coordinator, metrics=metrics)
        scheduler.register(identity)
        return ContinuousShadowRuntime(
            identity,
            client,
            ClosedCandleAggregator(identity, price_scale=100_000, max_seen_ticks=256),
            ingress,
            health,
            scheduler,
            dispatcher,
            clock,
            stale_after_seconds=5,
            metrics=metrics,
        )

    service = SupervisedShadowRuntime(worker, runtime_factory, clock=clock)
    host_clock = HostClock()
    host = ShadowRuntimeHost(
        host_clock,
        limits=ShadowHostLimits(
            maximum_series=1,
            maximum_actions_per_cycle=1,
            maximum_poll_timeout_seconds=0.5,
        ),
        restart_policy=RestartPolicy(
            max_crashes=3,
            window_seconds=10,
            base_delay_seconds=1,
            max_delay_seconds=1,
            open_seconds=2,
        ),
    )
    host.register(identity, service)
    try:
        assert host.start().state is ShadowHostState.RUNNING
        first_process = worker.process
        assert first_process is not None
        first_pid = first_process.pid
        assert len(repository.range((Broker.DERIV, "frxEURUSD", 60))) == 1

        first_process.kill()
        first_process.wait(timeout=2)
        deadline = time.monotonic() + 2
        while worker.health_state is WorkerHealthState.READY and time.monotonic() < deadline:
            time.sleep(0.01)
        degraded = host.run_cycle(poll_timeout=0.05)
        assert degraded.state is ShadowHostState.DEGRADED
        assert service.state is ShadowServiceState.RECOVERING
        assert health.snapshot(identity).health is MarketSeriesHealth.RECONNECTING

        host_clock.advance(1)
        assert host.run_cycle(poll_timeout=0.05).state is ShadowHostState.RUNNING
        second_process = worker.process
        assert second_process is not None
        assert second_process.pid != first_pid
        assert health.snapshot(identity).health is MarketSeriesHealth.HEALTHY

        for _ in range(70):
            host.run_cycle(poll_timeout=0.5)
            if metrics.live_candles_closed == 1:
                break
        candles = repository.range((Broker.DERIV, "frxEURUSD", 60))
        assert len(candles) == 2
        assert candles[1].open_time_ms == candles[0].close_time_ms
        assert metrics.subscription_restores == 2
        assert metrics.live_candles_closed == 1
        assert metrics.backfill_duplicates >= 1
        assert pipeline.close_times == [candle.close_time_ms for candle in candles]
        snapshot = service.snapshot()
        assert snapshot.state is ShadowServiceState.RUNNING
        assert snapshot.recovery_attempts == 1
        assert snapshot.poll_failures == 1
        host_snapshot = host.snapshot()
        assert host_snapshot.state is ShadowHostState.RUNNING
        assert host_snapshot.recovery_attempts == 1
        assert host_snapshot.recovery_failures == 0
        assert not (tmp_path / "state.db").exists()
    finally:
        host.shutdown()
        database.close()
