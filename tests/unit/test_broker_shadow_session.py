from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import pytest

from apps.core.broker_shadow_session import BrokerShadowSession
from apps.core.shadow_runtime import ReadOnlyMarketClient, ShadowServiceState
from apps.core.worker_supervisor import WorkerHealthState
from packages.domain.models import Broker
from packages.market_pipeline import LiveTickSource, MarketPipelineMetrics, MarketSeriesId


@dataclass(slots=True)
class FakeClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value


class FakeSupervisor:
    def __init__(self) -> None:
        self.health_state = WorkerHealthState.STOPPED
        self.starts = 0
        self.restarts = 0
        self.shutdowns = 0
        self.client = cast(ReadOnlyMarketClient, object())

    def start(self) -> ReadOnlyMarketClient:
        self.starts += 1
        self.health_state = WorkerHealthState.READY
        return self.client

    def restart(self) -> ReadOnlyMarketClient:
        self.restarts += 1
        self.health_state = WorkerHealthState.READY
        return self.client

    def shutdown(self, grace_seconds: float = 1.0) -> None:
        assert grace_seconds > 0
        self.shutdowns += 1
        self.health_state = WorkerHealthState.STOPPED


class FakeRuntime:
    def __init__(self, series_id: MarketSeriesId) -> None:
        self.series_id = series_id
        self.metrics = MarketPipelineMetrics()
        self.subscribed = False
        self.starts = 0
        self.recoveries = 0
        self.polls = 0
        self.disconnects = 0
        self.stops = 0

    def start(self) -> bool:
        self.starts += 1
        self.subscribed = True
        return True

    def recover_and_restore(self) -> bool:
        self.recoveries += 1
        self.subscribed = True
        return True

    def poll_once(self, *, timeout: float) -> None:
        assert timeout > 0
        self.polls += 1

    def on_disconnect(self) -> int:
        self.disconnects += 1
        self.subscribed = False
        return self.disconnects

    def stop(self) -> None:
        self.stops += 1
        self.subscribed = False


def series(symbol: str) -> MarketSeriesId:
    return MarketSeriesId(Broker.DERIV, symbol, symbol, "OPTION", 60)


def runtime_factory(
    created: list[FakeRuntime],
):
    def factory(
        _client: ReadOnlyMarketClient,
        _source: LiveTickSource,
        series_id: MarketSeriesId,
    ) -> FakeRuntime:
        runtime = FakeRuntime(series_id)
        created.append(runtime)
        return runtime

    return factory


def test_broker_shadow_session_starts_once_and_polls_series_fairly() -> None:
    supervisor = FakeSupervisor()
    clock = FakeClock()
    created: list[FakeRuntime] = []
    session = BrokerShadowSession(Broker.DERIV, supervisor, clock=clock)
    session.register(series("frxEURUSD"), runtime_factory(created))
    session.register(series("frxGBPUSD"), runtime_factory(created))

    assert session.start()
    for _ in range(5):
        session.poll_once(timeout=0.1)
    clock.value = 3.0
    snapshot = session.snapshot()

    assert supervisor.starts == 1
    assert session.state is ShadowServiceState.RUNNING
    assert snapshot.registered_series == 2
    assert snapshot.subscribed_series == 2
    assert snapshot.poll_count == 5
    assert snapshot.elapsed_monotonic_seconds == 3.0
    assert [runtime.polls for runtime in created] == [3, 2]
    assert snapshot.router is not None
    assert snapshot.router.registered_series == 2

    session.shutdown()
    assert supervisor.shutdowns == 1
    assert session.state is ShadowServiceState.STOPPED
    assert all(runtime.stops == 1 for runtime in created)


def test_broker_shadow_session_worker_loss_disconnects_all_series_and_recovers_once() -> None:
    supervisor = FakeSupervisor()
    created: list[FakeRuntime] = []
    session = BrokerShadowSession(Broker.DERIV, supervisor)
    factory = runtime_factory(created)
    session.register(series("frxEURUSD"), factory)
    session.register(series("frxGBPUSD"), factory)
    assert session.start()

    supervisor.health_state = WorkerHealthState.DISCONNECTED
    assert session.poll_once(timeout=0.1) is None
    assert session.state is ShadowServiceState.RECOVERING
    assert session.recover()

    assert supervisor.restarts == 1
    assert session.state is ShadowServiceState.RUNNING
    assert len(created) == 4
    assert [runtime.disconnects for runtime in created[:2]] == [1, 1]
    assert [runtime.stops for runtime in created[:2]] == [1, 1]
    assert [runtime.recoveries for runtime in created[2:]] == [1, 1]


def test_broker_shadow_session_rejects_unsafe_registration_and_usage() -> None:
    session = BrokerShadowSession(Broker.DERIV, FakeSupervisor(), max_series=1)
    eurusd = series("frxEURUSD")
    session.register(eurusd, runtime_factory([]))
    with pytest.raises(ValueError, match="already"):
        session.register(eurusd, runtime_factory([]))
    with pytest.raises(ValueError, match="limit"):
        session.register(series("frxGBPUSD"), runtime_factory([]))
    with pytest.raises(ValueError, match="broker"):
        BrokerShadowSession(Broker.DERIV, FakeSupervisor()).register(
            MarketSeriesId(Broker.IQ_OPTION, "EURUSD", "EURUSD", "OPTION", 60),
            runtime_factory([]),
        )
    with pytest.raises(ValueError, match="positive"):
        session.poll_once(timeout=0)
    with pytest.raises(RuntimeError, match="NOT_STARTED"):
        session.poll_once(timeout=0.1)
