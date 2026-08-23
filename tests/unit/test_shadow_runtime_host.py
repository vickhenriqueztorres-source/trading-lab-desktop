from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import pytest

from apps.core.shadow_host import (
    ProcessResourceSample,
    ShadowHostLimits,
    ShadowHostState,
    ShadowRuntimeHost,
    SystemResourceProbe,
)
from apps.core.shadow_runtime import ShadowServiceSnapshot, ShadowServiceState
from apps.core.worker_supervisor import CircuitState, RestartPolicy, WorkerHealthState
from packages.domain.models import Broker
from packages.market_pipeline import MarketSeriesId


@dataclass
class FakeClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class StableResourceProbe:
    def __init__(self, clock: FakeClock, *, rss_bytes: int = 100) -> None:
        self._clock = clock
        self._rss = rss_bytes
        self._cpu = 0.0

    def sample(self) -> ProcessResourceSample:
        self._cpu += 0.000_01
        return ProcessResourceSample(self._clock.now(), self._cpu, self._rss)


class SequenceResourceProbe:
    def __init__(self, samples: tuple[ProcessResourceSample, ...]) -> None:
        self._samples = deque(samples)

    def sample(self) -> ProcessResourceSample:
        return self._samples.popleft()


class FakeHostedService:
    def __init__(
        self,
        *,
        fail_next_poll: bool = False,
        recovery_outcomes: tuple[bool, ...] = (),
        lag_ms: int = 0,
    ) -> None:
        self._state = ShadowServiceState.STOPPED
        self.fail_next_poll = fail_next_poll
        self.recovery_outcomes = deque(recovery_outcomes)
        self.lag_ms = lag_ms
        self.polls = 0
        self.recoveries = 0
        self.shutdowns = 0

    @property
    def state(self) -> ShadowServiceState:
        return self._state

    def snapshot(self) -> ShadowServiceSnapshot:
        return ShadowServiceSnapshot(
            state=self._state,
            worker_health=(
                WorkerHealthState.READY
                if self._state is ShadowServiceState.RUNNING
                else WorkerHealthState.STOPPED
            ),
            subscribed=self._state is ShadowServiceState.RUNNING,
            start_attempts=1,
            recovery_attempts=self.recoveries,
            poll_count=self.polls,
            poll_failures=int(self.fail_next_poll),
            elapsed_monotonic_seconds=0,
            live_dispatch_lag_ms_max=self.lag_ms,
        )

    def start(self) -> bool:
        self._state = ShadowServiceState.RUNNING
        return True

    def poll_once(self, *, timeout: float) -> None:
        assert timeout > 0
        self.polls += 1
        if self.fail_next_poll:
            self.fail_next_poll = False
            self._state = ShadowServiceState.RECOVERING
            raise ConnectionError("simulated hosted poll failure")

    def recover(self) -> bool:
        self.recoveries += 1
        ready = self.recovery_outcomes.popleft() if self.recovery_outcomes else True
        self._state = ShadowServiceState.RUNNING if ready else ShadowServiceState.FAILED
        return ready

    def shutdown(self) -> None:
        self.shutdowns += 1
        self._state = ShadowServiceState.STOPPED


def series(index: int) -> MarketSeriesId:
    return MarketSeriesId(
        Broker.DERIV,
        f"R_{index}",
        f"R_{index}",
        "OPTION",
        60,
    )


def test_host_soak_10000_cycles_is_bounded_and_fair_across_series() -> None:
    clock = FakeClock()
    services = tuple(FakeHostedService(lag_ms=index) for index in range(3))
    host = ShadowRuntimeHost(
        clock,
        limits=ShadowHostLimits(
            maximum_series=3,
            maximum_actions_per_cycle=2,
            maximum_poll_timeout_seconds=0.1,
            maximum_rss_bytes=1_000,
            maximum_cpu_seconds_per_cycle=0.001,
            maximum_live_dispatch_lag_ms=10,
        ),
        resource_probe=StableResourceProbe(clock),
    )
    for index, service in enumerate(services):
        host.register(series(index), service)
    assert host.start().state is ShadowHostState.RUNNING

    for _ in range(10_000):
        host.run_cycle(poll_timeout=0.001, maximum_actions=2)
        clock.advance(0.001)

    snapshot = host.snapshot()
    assert snapshot.state is ShadowHostState.RUNNING
    assert snapshot.cycles == 10_000
    assert snapshot.actions == 20_000
    assert snapshot.maximum_observed_rss_bytes == 100
    assert snapshot.maximum_observed_live_dispatch_lag_ms == 2
    poll_counts = tuple(service.polls for service in services)
    assert max(poll_counts) - min(poll_counts) <= 1
    assert sum(poll_counts) == 20_000
    assert host.shutdown().state is ShadowHostState.STOPPED
    assert all(service.shutdowns == 1 for service in services)


def test_host_isolates_failure_and_uses_backoff_open_half_open_recovery() -> None:
    clock = FakeClock()
    flaky = FakeHostedService(fail_next_poll=True, recovery_outcomes=(False, True))
    healthy = FakeHostedService()
    host = ShadowRuntimeHost(
        clock,
        limits=ShadowHostLimits(maximum_actions_per_cycle=2),
        restart_policy=RestartPolicy(
            max_crashes=2,
            window_seconds=30,
            base_delay_seconds=1,
            max_delay_seconds=4,
            open_seconds=5,
        ),
        resource_probe=StableResourceProbe(clock),
    )
    host.register(series(1), flaky)
    host.register(series(2), healthy)
    host.start()

    first = host.run_cycle(poll_timeout=0.01)
    assert first.state is ShadowHostState.DEGRADED
    assert first.poll_failures == 1
    assert healthy.polls == 1
    assert first.series[0].next_recovery_monotonic == 1

    clock.advance(1)
    opened = host.run_cycle(poll_timeout=0.01)
    assert opened.series[0].circuit_state is CircuitState.OPEN
    assert opened.recovery_attempts == 1
    assert opened.recovery_failures == 1
    assert healthy.polls == 2

    clock.advance(4)
    waiting = host.run_cycle(poll_timeout=0.01)
    assert waiting.series[0].circuit_state is CircuitState.OPEN
    assert flaky.recoveries == 1
    assert healthy.polls == 3

    clock.advance(1)
    recovered = host.run_cycle(poll_timeout=0.01)
    assert recovered.state is ShadowHostState.RUNNING
    assert recovered.series[0].circuit_state is CircuitState.CLOSED
    assert flaky.recoveries == 2
    assert healthy.polls == 4


def test_host_resource_budget_exhaustion_stops_every_series() -> None:
    clock = FakeClock()
    probe = SequenceResourceProbe(
        (
            ProcessResourceSample(0, 0.0, 100),
            ProcessResourceSample(0, 0.1, 100),
            ProcessResourceSample(1, 0.2, 100),
            ProcessResourceSample(1, 0.3, 1_000),
        )
    )
    services = (FakeHostedService(), FakeHostedService())
    host = ShadowRuntimeHost(
        clock,
        limits=ShadowHostLimits(maximum_rss_bytes=500),
        resource_probe=probe,
    )
    for index, service in enumerate(services):
        host.register(series(index), service)
    host.start()
    clock.advance(1)
    snapshot = host.run_cycle(poll_timeout=0.01)
    assert snapshot.state is ShadowHostState.RESOURCE_EXHAUSTED
    assert snapshot.reason_code == "SHADOW_RSS_LIMIT_EXCEEDED"
    assert snapshot.maximum_observed_rss_bytes == 1_000
    assert all(service.state is ShadowServiceState.STOPPED for service in services)


def test_host_cpu_budget_exhaustion_is_measured_per_cycle() -> None:
    clock = FakeClock()
    probe = SequenceResourceProbe(
        (
            ProcessResourceSample(0, 0.0, 100),
            ProcessResourceSample(0, 0.1, 100),
            ProcessResourceSample(1, 0.2, 100),
            ProcessResourceSample(1, 1.0, 100),
        )
    )
    service = FakeHostedService()
    host = ShadowRuntimeHost(
        clock,
        limits=ShadowHostLimits(maximum_cpu_seconds_per_cycle=0.5),
        resource_probe=probe,
    )
    host.register(series(1), service)
    host.start()
    clock.advance(1)
    snapshot = host.run_cycle(poll_timeout=0.01)
    assert snapshot.state is ShadowHostState.RESOURCE_EXHAUSTED
    assert snapshot.reason_code == "SHADOW_CPU_LIMIT_EXCEEDED"
    assert service.state is ShadowServiceState.STOPPED


def test_host_lag_budget_blocks_before_starting_series() -> None:
    clock = FakeClock()
    service = FakeHostedService(lag_ms=101)
    host = ShadowRuntimeHost(
        clock,
        limits=ShadowHostLimits(maximum_live_dispatch_lag_ms=100),
        resource_probe=StableResourceProbe(clock),
    )
    host.register(series(1), service)
    snapshot = host.start()
    assert snapshot.state is ShadowHostState.RESOURCE_EXHAUSTED
    assert snapshot.reason_code == "SHADOW_LAG_LIMIT_EXCEEDED"
    assert service.state is ShadowServiceState.STOPPED


def test_host_rejects_unbounded_configuration_and_system_probe_is_safe() -> None:
    clock = FakeClock()
    host = ShadowRuntimeHost(
        clock,
        limits=ShadowHostLimits(maximum_series=1, maximum_actions_per_cycle=1),
        resource_probe=StableResourceProbe(clock),
    )
    host.register(series(1), FakeHostedService())
    with pytest.raises(ValueError, match="series limit"):
        host.register(series(2), FakeHostedService())
    host.start()
    with pytest.raises(ValueError, match="poll timeout"):
        host.run_cycle(poll_timeout=2)
    with pytest.raises(ValueError, match="action count"):
        host.run_cycle(poll_timeout=0.1, maximum_actions=2)
    with pytest.raises(ValueError, match="action count"):
        host.run_cycle(poll_timeout=0.1, maximum_actions=0)
    host.shutdown()

    sample = SystemResourceProbe(clock).sample()
    assert sample.process_cpu_seconds >= 0
    assert sample.rss_bytes is None or sample.rss_bytes > 0
