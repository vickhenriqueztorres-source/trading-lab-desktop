from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from apps.core.shadow_runtime import (
    ReadOnlyMarketClient,
    ShadowServiceState,
    SupervisedShadowRuntime,
)
from apps.core.worker_supervisor import WorkerHealthState
from packages.market_pipeline import MarketPipelineMetrics


@dataclass
class FakeClock:
    value: float = 10.0

    def now(self) -> float:
        return self.value


class FakeRuntime:
    def __init__(self, *, fail_recovery: bool = False) -> None:
        self.metrics = MarketPipelineMetrics()
        self.subscribed = False
        self.disconnects = 0
        self.polls = 0
        self.stops = 0
        self.fail_recovery = fail_recovery

    def start(self) -> bool:
        self.subscribed = True
        return True

    def recover_and_restore(self) -> bool:
        if self.fail_recovery:
            raise RuntimeError("simulated recovery failure")
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


def test_supervised_shadow_runtime_blocks_on_worker_loss_and_recovers_explicitly() -> None:
    supervisor = FakeSupervisor()
    runtimes: list[FakeRuntime] = []
    clock = FakeClock()

    def factory(_client: ReadOnlyMarketClient) -> FakeRuntime:
        runtime = FakeRuntime()
        runtimes.append(runtime)
        return runtime

    service = SupervisedShadowRuntime(supervisor, factory, clock=clock)
    assert service.start()
    service.poll_once(timeout=0.1)
    assert runtimes[0].polls == 1

    supervisor.health_state = WorkerHealthState.DISCONNECTED
    assert service.poll_once(timeout=0.1) is None
    assert service.state is ShadowServiceState.RECOVERING
    assert runtimes[0].disconnects == 1

    assert service.recover()
    assert supervisor.restarts == 1
    assert len(runtimes) == 2
    assert runtimes[1].subscribed
    clock.value = 15.0
    snapshot = service.snapshot()
    assert snapshot.state is ShadowServiceState.RUNNING
    assert snapshot.start_attempts == 1
    assert snapshot.recovery_attempts == 1
    assert snapshot.poll_count == 1
    assert snapshot.poll_failures == 1
    assert snapshot.elapsed_monotonic_seconds == 5.0

    service.shutdown()
    assert service.state is ShadowServiceState.STOPPED
    assert supervisor.shutdowns == 1


def test_supervised_shadow_runtime_requires_start_and_positive_timeout() -> None:
    service = SupervisedShadowRuntime(FakeSupervisor(), lambda _client: FakeRuntime())
    try:
        service.poll_once(timeout=0.1)
    except RuntimeError as exc:
        assert str(exc) == "SHADOW_SERVICE_NOT_STARTED"
    else:
        raise AssertionError("poll before start must fail")

    try:
        service.poll_once(timeout=0)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("non-positive timeout must fail")


def test_supervised_shadow_runtime_cleans_new_worker_when_recovery_fails() -> None:
    supervisor = FakeSupervisor()
    runtimes = [FakeRuntime(), FakeRuntime(fail_recovery=True)]
    service = SupervisedShadowRuntime(supervisor, lambda _client: runtimes.pop(0))
    assert service.start()
    supervisor.health_state = WorkerHealthState.DISCONNECTED
    assert service.poll_once(timeout=0.1) is None

    try:
        service.recover()
    except RuntimeError as exc:
        assert str(exc) == "simulated recovery failure"
    else:
        raise AssertionError("failed recovery must propagate")
    assert service.state is ShadowServiceState.FAILED
    assert supervisor.shutdowns == 1
    assert supervisor.health_state is WorkerHealthState.STOPPED
