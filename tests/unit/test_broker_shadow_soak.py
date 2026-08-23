from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass

import pytest

from apps.core.broker_shadow_session import BrokerShadowSeriesSnapshot, BrokerShadowSessionSnapshot
from apps.core.broker_shadow_soak import (
    BrokerShadowSoakLimits,
    BrokerShadowSoakRunner,
    BrokerShadowSoakState,
    BrokerShadowTemporalSoakMatrixRunner,
    BrokerShadowTemporalSoakOutcome,
    BrokerShadowTemporalSoakPlan,
    BrokerShadowTemporalSoakReport,
    BrokerShadowTemporalSoakRunner,
    BrokerShadowTemporalSoakScenario,
    ChildProcessResourceSample,
    NoChildProcessProbe,
)
from apps.core.shadow_host import ProcessResourceSample
from apps.core.shadow_runtime import ShadowServiceState
from apps.core.worker_supervisor import WorkerHealthState
from packages.domain.models import Broker
from packages.market_pipeline import MarketSeriesId


@dataclass(slots=True)
class FakeClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class StableResourceProbe:
    def __init__(self, clock: FakeClock, *, rss_bytes: int = 100) -> None:
        self._clock = clock
        self._rss_bytes = rss_bytes
        self._cpu = 0.0

    def sample(self) -> ProcessResourceSample:
        self._cpu += 0.000_01
        return ProcessResourceSample(self._clock.now(), self._cpu, self._rss_bytes)


class StableChildProbe:
    def __init__(self, *, pid: int = 4242, rss_bytes: int | None = 200) -> None:
        self._pid = pid
        self._rss_bytes = rss_bytes

    def sample(self) -> ChildProcessResourceSample:
        return ChildProcessResourceSample(self._pid, alive=True, rss_bytes=self._rss_bytes)


class SequenceChildProbe:
    def __init__(self, samples: tuple[ChildProcessResourceSample, ...]) -> None:
        self._samples = deque(samples)
        self._last = samples[-1]

    def sample(self) -> ChildProcessResourceSample:
        if self._samples:
            self._last = self._samples.popleft()
        return self._last


class FakeSoakSession:
    def __init__(self, *, lag_ms: int = 0) -> None:
        self._state = ShadowServiceState.STOPPED
        self.lag_ms = lag_ms
        self.starts = 0
        self.polls = 0
        self.poll_failures = 0
        self.recoveries = 0
        self.shutdowns = 0

    @property
    def state(self) -> ShadowServiceState:
        return self._state

    def force_recovering(self) -> None:
        self._state = ShadowServiceState.RECOVERING

    def snapshot(self) -> BrokerShadowSessionSnapshot:
        return BrokerShadowSessionSnapshot(
            broker=Broker.DERIV,
            state=self._state,
            worker_health=(
                WorkerHealthState.READY
                if self._state is ShadowServiceState.RUNNING
                else WorkerHealthState.DISCONNECTED
            ),
            registered_series=1,
            subscribed_series=int(self._state is ShadowServiceState.RUNNING),
            start_attempts=self.starts,
            recovery_attempts=self.recoveries,
            poll_count=self.polls,
            poll_failures=self.poll_failures,
            elapsed_monotonic_seconds=0.0,
            router=None,
            series=(
                BrokerShadowSeriesSnapshot(
                    series_id=MarketSeriesId(Broker.DERIV, "frxEURUSD", "frxEURUSD", "OPTION", 60),
                    subscribed=self._state is ShadowServiceState.RUNNING,
                    poll_count=self.polls,
                    poll_failures=self.poll_failures,
                    live_dispatch_lag_ms_max=self.lag_ms,
                ),
            ),
        )

    def start(self) -> bool:
        self.starts += 1
        self._state = ShadowServiceState.RUNNING
        return True

    def poll_once(self, *, timeout: float) -> None:
        assert timeout > 0
        self.polls += 1

    def recover(self) -> bool:
        self.recoveries += 1
        self._state = ShadowServiceState.RUNNING
        return True

    def shutdown(self) -> None:
        self.shutdowns += 1
        self._state = ShadowServiceState.STOPPED


class RaisingTemporalSoakRunner(BrokerShadowTemporalSoakRunner):
    def run(self) -> BrokerShadowTemporalSoakReport:
        raise RuntimeError("raw transport detail should not leak")


class RaisingShutdownTemporalSoakRunner(RaisingTemporalSoakRunner):
    def shutdown(self):
        super().shutdown()
        raise RuntimeError("raw shutdown detail should not leak")


def test_soak_runner_completes_bounded_cycles_with_core_and_child_telemetry() -> None:
    clock = FakeClock()
    session = FakeSoakSession()
    runner = BrokerShadowSoakRunner(
        session,
        clock=clock,
        limits=BrokerShadowSoakLimits(max_cycles=5, poll_timeout_seconds=0.01),
        resource_probe=StableResourceProbe(clock, rss_bytes=150),
        child_process_probe=StableChildProbe(pid=4321, rss_bytes=250),
    )

    snapshot = runner.run_until_complete()

    assert snapshot.state is BrokerShadowSoakState.COMPLETED
    assert snapshot.cycles == 5
    assert snapshot.poll_attempts == 5
    assert session.polls == 5
    assert session.starts == 1
    assert snapshot.maximum_core_rss_bytes == 150
    assert snapshot.maximum_child_rss_bytes == 250
    assert snapshot.latest_resources is not None
    assert snapshot.latest_resources.child_pid == 4321
    assert snapshot.latest_resources.child_process_alive


def test_soak_runner_recovers_after_injected_suspension_without_auto_retrying_orders() -> None:
    clock = FakeClock()
    session = FakeSoakSession()

    def suspend_on_second_cycle(cycle: int, _session: FakeSoakSession) -> None:
        if cycle == 2:
            session.force_recovering()

    runner = BrokerShadowSoakRunner(
        session,
        clock=clock,
        limits=BrokerShadowSoakLimits(max_cycles=4, poll_timeout_seconds=0.01, max_recoveries=1),
        resource_probe=StableResourceProbe(clock),
        child_process_probe=StableChildProbe(),
        before_cycle=suspend_on_second_cycle,
    )

    snapshot = runner.run_until_complete()

    assert snapshot.state is BrokerShadowSoakState.COMPLETED
    assert snapshot.recovery_attempts == 1
    assert session.recoveries == 1
    assert session.polls == 3


def test_soak_runner_resource_exhaustion_stops_session_closed() -> None:
    clock = FakeClock()
    session = FakeSoakSession()
    runner = BrokerShadowSoakRunner(
        session,
        clock=clock,
        limits=BrokerShadowSoakLimits(max_cycles=3, max_child_rss_bytes=500),
        resource_probe=StableResourceProbe(clock),
        child_process_probe=SequenceChildProbe(
            (
                ChildProcessResourceSample(1001, alive=True, rss_bytes=600),
                ChildProcessResourceSample(1001, alive=True, rss_bytes=600),
            )
        ),
    )

    snapshot = runner.run_cycle()

    assert snapshot.state is BrokerShadowSoakState.RESOURCE_EXHAUSTED
    assert snapshot.reason_code == "BROKER_SHADOW_SOAK_CHILD_RSS_LIMIT_EXCEEDED"
    assert session.shutdowns == 1
    assert session.state is ShadowServiceState.STOPPED


def test_soak_runner_enforces_recovery_limit_and_lag_budget() -> None:
    clock = FakeClock()
    recovering = FakeSoakSession()

    def suspend_on_first_cycle(_cycle: int, _session: FakeSoakSession) -> None:
        recovering.force_recovering()

    recovery_limited = BrokerShadowSoakRunner(
        recovering,
        clock=clock,
        limits=BrokerShadowSoakLimits(max_cycles=2, max_recoveries=0),
        resource_probe=StableResourceProbe(clock),
        child_process_probe=NoChildProcessProbe(),
        before_cycle=suspend_on_first_cycle,
    )

    limited_snapshot = recovery_limited.run_cycle()

    assert limited_snapshot.state is BrokerShadowSoakState.FAILED
    assert limited_snapshot.reason_code == "BROKER_SHADOW_SOAK_RECOVERY_LIMIT_EXCEEDED"
    assert recovering.shutdowns == 1

    lagged = FakeSoakSession(lag_ms=101)
    lag_runner = BrokerShadowSoakRunner(
        lagged,
        clock=clock,
        limits=BrokerShadowSoakLimits(max_cycles=2, max_live_dispatch_lag_ms=100),
        resource_probe=StableResourceProbe(clock),
    )

    lag_snapshot = lag_runner.run_cycle()

    assert lag_snapshot.state is BrokerShadowSoakState.RESOURCE_EXHAUSTED
    assert lag_snapshot.reason_code == "BROKER_SHADOW_SOAK_LAG_LIMIT_EXCEEDED"
    assert lagged.shutdowns == 1


def test_soak_runner_rejects_unbounded_limits() -> None:
    with pytest.raises(ValueError, match="cycle"):
        BrokerShadowSoakLimits(max_cycles=0)
    with pytest.raises(ValueError, match="poll timeout"):
        BrokerShadowSoakLimits(max_cycles=1, poll_timeout_seconds=0)
    with pytest.raises(ValueError, match="recovery"):
        BrokerShadowSoakLimits(max_cycles=1, max_recoveries=-1)
    with pytest.raises(ValueError, match="resource"):
        BrokerShadowSoakLimits(max_cycles=1, max_core_rss_bytes=0)


def test_temporal_soak_passes_window_and_writes_redacted_json_report(tmp_path) -> None:
    clock = FakeClock()
    session = FakeSoakSession()
    runner = BrokerShadowSoakRunner(
        session,
        clock=clock,
        limits=BrokerShadowSoakLimits(max_cycles=20, poll_timeout_seconds=0.01),
        resource_probe=StableResourceProbe(clock, rss_bytes=150),
        child_process_probe=StableChildProbe(pid=4321, rss_bytes=250),
    )
    temporal = BrokerShadowTemporalSoakRunner(
        runner,
        BrokerShadowTemporalSoakPlan(
            duration_seconds=5.0,
            minimum_cycles=5,
            maximum_cycles=20,
            sample_every_cycles=2,
            max_samples=3,
        ),
        clock=clock,
        after_cycle=lambda _snapshot: clock.advance(1.0),
    )

    report = temporal.run()
    report_path = tmp_path / "broker-shadow-soak-report.json"
    report.write_json(report_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert report.outcome is BrokerShadowTemporalSoakOutcome.PASSED
    assert report.elapsed_monotonic_seconds == 5.0
    assert report.duration_reached
    assert report.final_snapshot.cycles == 5
    assert report.shutdown_snapshot.state is BrokerShadowSoakState.STOPPED
    assert len(report.samples) == 3
    assert payload["outcome"] == "PASSED"
    assert payload["final_snapshot"]["session"]["broker"] == "DERIV"
    assert payload["final_snapshot"]["session"]["series"][0]["series_id"] == (
        "DERIV:frxEURUSD:frxEURUSD:OPTION:60:PUBLIC_MARKET"
    )
    serialized = json.dumps(payload)
    assert "ORDER_SUBMIT" not in serialized
    assert "TradeIntent" not in serialized
    assert "RiskReservation" not in serialized


def test_temporal_soak_fails_closed_when_duration_does_not_reach_before_cycle_limit() -> None:
    clock = FakeClock()
    session = FakeSoakSession()
    runner = BrokerShadowSoakRunner(
        session,
        clock=clock,
        limits=BrokerShadowSoakLimits(max_cycles=100),
        resource_probe=StableResourceProbe(clock),
    )
    temporal = BrokerShadowTemporalSoakRunner(
        runner,
        BrokerShadowTemporalSoakPlan(
            duration_seconds=10.0,
            minimum_cycles=2,
            maximum_cycles=3,
        ),
        clock=clock,
        after_cycle=lambda _snapshot: clock.advance(1.0),
    )

    report = temporal.run()

    assert report.outcome is BrokerShadowTemporalSoakOutcome.FAILED
    assert report.reason_code == "BROKER_SHADOW_TEMPORAL_SOAK_DURATION_NOT_REACHED"
    assert report.final_snapshot.cycles == 3
    assert report.shutdown_snapshot.state is BrokerShadowSoakState.STOPPED
    assert session.state is ShadowServiceState.STOPPED


def test_temporal_soak_enforces_acceptance_limits_and_bounded_samples() -> None:
    clock = FakeClock()
    session = FakeSoakSession()

    def suspend_once(cycle: int, _session: FakeSoakSession) -> None:
        if cycle == 2:
            session.force_recovering()

    runner = BrokerShadowSoakRunner(
        session,
        clock=clock,
        limits=BrokerShadowSoakLimits(max_cycles=20, max_recoveries=2),
        resource_probe=StableResourceProbe(clock),
        before_cycle=suspend_once,
    )
    temporal = BrokerShadowTemporalSoakRunner(
        runner,
        BrokerShadowTemporalSoakPlan(
            duration_seconds=4.0,
            minimum_cycles=4,
            maximum_cycles=20,
            sample_every_cycles=1,
            max_samples=2,
            max_recovery_attempts=0,
        ),
        clock=clock,
        after_cycle=lambda _snapshot: clock.advance(1.0),
    )

    report = temporal.run()

    assert report.outcome is BrokerShadowTemporalSoakOutcome.FAILED
    assert report.reason_code == "BROKER_SHADOW_TEMPORAL_SOAK_RECOVERY_LIMIT_EXCEEDED"
    assert report.final_snapshot.recovery_attempts == 1
    assert len(report.samples) == 2
    assert report.dropped_sample_count >= 2


def test_temporal_soak_plan_rejects_unbounded_configuration() -> None:
    with pytest.raises(ValueError, match="duration"):
        BrokerShadowTemporalSoakPlan(duration_seconds=0, minimum_cycles=1, maximum_cycles=1)
    with pytest.raises(ValueError, match="minimum"):
        BrokerShadowTemporalSoakPlan(duration_seconds=1, minimum_cycles=0, maximum_cycles=1)
    with pytest.raises(ValueError, match="maximum"):
        BrokerShadowTemporalSoakPlan(duration_seconds=1, minimum_cycles=2, maximum_cycles=1)
    with pytest.raises(ValueError, match="sample"):
        BrokerShadowTemporalSoakPlan(
            duration_seconds=1,
            minimum_cycles=1,
            maximum_cycles=1,
            sample_every_cycles=0,
        )


def test_temporal_soak_matrix_compares_baseline_and_recovery_and_writes_redacted_json(
    tmp_path,
) -> None:
    clock = FakeClock()
    baseline_session = FakeSoakSession()
    baseline = BrokerShadowTemporalSoakScenario(
        "baseline-1s",
        BrokerShadowTemporalSoakRunner(
            BrokerShadowSoakRunner(
                baseline_session,
                clock=clock,
                limits=BrokerShadowSoakLimits(max_cycles=10),
                resource_probe=StableResourceProbe(clock),
            ),
            BrokerShadowTemporalSoakPlan(
                duration_seconds=2.0,
                minimum_cycles=2,
                maximum_cycles=4,
            ),
            clock=clock,
            after_cycle=lambda _snapshot: clock.advance(1.0),
        ),
    )
    recovering_session = FakeSoakSession()

    def suspend_once(cycle: int, _session: FakeSoakSession) -> None:
        if cycle == 2:
            recovering_session.force_recovering()

    recovery = BrokerShadowTemporalSoakScenario(
        "suspend-once-500ms",
        BrokerShadowTemporalSoakRunner(
            BrokerShadowSoakRunner(
                recovering_session,
                clock=clock,
                limits=BrokerShadowSoakLimits(max_cycles=10, max_recoveries=1),
                resource_probe=StableResourceProbe(clock),
                before_cycle=suspend_once,
            ),
            BrokerShadowTemporalSoakPlan(
                duration_seconds=1.5,
                minimum_cycles=3,
                maximum_cycles=6,
                max_recovery_attempts=1,
            ),
            clock=clock,
            after_cycle=lambda _snapshot: clock.advance(0.5),
        ),
    )
    matrix = BrokerShadowTemporalSoakMatrixRunner(
        (baseline, recovery),
        maximum_scenarios=4,
        clock=clock,
    )

    report = matrix.run()
    report_path = tmp_path / "broker-shadow-temporal-soak-matrix.json"
    report.write_json(report_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert report.outcome is BrokerShadowTemporalSoakOutcome.PASSED
    assert report.passed_scenario_count == 2
    assert report.failed_scenario_count == 0
    assert [result.scenario_id for result in report.results] == [
        "baseline-1s",
        "suspend-once-500ms",
    ]
    assert recovering_session.recoveries == 1
    assert baseline_session.state is ShadowServiceState.STOPPED
    assert recovering_session.state is ShadowServiceState.STOPPED
    assert payload["scenario_count"] == 2
    assert payload["results"][0]["report"]["plan"]["duration_seconds"] == 2.0
    assert payload["results"][1]["report"]["plan"]["duration_seconds"] == 1.5
    serialized = json.dumps(payload)
    assert "ORDER_SUBMIT" not in serialized
    assert "TradeIntent" not in serialized
    assert "RiskReservation" not in serialized


def test_temporal_soak_matrix_keeps_comparing_after_a_failed_scenario() -> None:
    clock = FakeClock()
    duration_limited_session = FakeSoakSession()
    duration_limited = BrokerShadowTemporalSoakScenario(
        "duration-limited",
        BrokerShadowTemporalSoakRunner(
            BrokerShadowSoakRunner(
                duration_limited_session,
                clock=clock,
                limits=BrokerShadowSoakLimits(max_cycles=10),
                resource_probe=StableResourceProbe(clock),
            ),
            BrokerShadowTemporalSoakPlan(
                duration_seconds=5.0,
                minimum_cycles=2,
                maximum_cycles=2,
            ),
            clock=clock,
            after_cycle=lambda _snapshot: clock.advance(1.0),
        ),
    )
    final_session = FakeSoakSession()
    final_scenario = BrokerShadowTemporalSoakScenario(
        "baseline-after-failure",
        BrokerShadowTemporalSoakRunner(
            BrokerShadowSoakRunner(
                final_session,
                clock=clock,
                limits=BrokerShadowSoakLimits(max_cycles=10),
                resource_probe=StableResourceProbe(clock),
            ),
            BrokerShadowTemporalSoakPlan(
                duration_seconds=1.0,
                minimum_cycles=1,
                maximum_cycles=2,
            ),
            clock=clock,
            after_cycle=lambda _snapshot: clock.advance(1.0),
        ),
    )

    report = BrokerShadowTemporalSoakMatrixRunner(
        (duration_limited, final_scenario),
        clock=clock,
    ).run()

    assert report.outcome is BrokerShadowTemporalSoakOutcome.FAILED
    assert report.reason_code == "BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_FAILED"
    assert report.passed_scenario_count == 1
    assert report.failed_scenario_count == 1
    assert report.results[0].reason_code == ("BROKER_SHADOW_TEMPORAL_SOAK_DURATION_NOT_REACHED")
    assert report.results[1].outcome is BrokerShadowTemporalSoakOutcome.PASSED
    assert final_session.polls == 1


def test_temporal_soak_matrix_redacts_exception_and_shuts_down_before_continuing() -> None:
    clock = FakeClock()
    exploding_session = FakeSoakSession()
    exploding_runner = BrokerShadowSoakRunner(
        exploding_session,
        clock=clock,
        limits=BrokerShadowSoakLimits(max_cycles=2),
        resource_probe=StableResourceProbe(clock),
    )
    exploding_runner.start()
    exploding = BrokerShadowTemporalSoakScenario(
        "transport-crash",
        RaisingTemporalSoakRunner(
            exploding_runner,
            BrokerShadowTemporalSoakPlan(
                duration_seconds=1.0,
                minimum_cycles=1,
                maximum_cycles=2,
            ),
            clock=clock,
        ),
    )
    healthy_session = FakeSoakSession()
    healthy = BrokerShadowTemporalSoakScenario(
        "healthy-after-crash",
        BrokerShadowTemporalSoakRunner(
            BrokerShadowSoakRunner(
                healthy_session,
                clock=clock,
                limits=BrokerShadowSoakLimits(max_cycles=2),
                resource_probe=StableResourceProbe(clock),
            ),
            BrokerShadowTemporalSoakPlan(
                duration_seconds=1.0,
                minimum_cycles=1,
                maximum_cycles=2,
            ),
            clock=clock,
            after_cycle=lambda _snapshot: clock.advance(1.0),
        ),
    )

    report = BrokerShadowTemporalSoakMatrixRunner((exploding, healthy), clock=clock).run()
    serialized = json.dumps(report.to_payload())

    assert report.outcome is BrokerShadowTemporalSoakOutcome.FAILED
    assert report.reason_code == "BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_RAISED"
    assert report.results[0].report is None
    assert report.results[1].outcome is BrokerShadowTemporalSoakOutcome.PASSED
    assert exploding_session.state is ShadowServiceState.STOPPED
    assert exploding_session.shutdowns == 1
    assert "raw transport detail should not leak" not in serialized


def test_temporal_soak_matrix_reports_shutdown_failure_and_keeps_comparing() -> None:
    clock = FakeClock()
    failing_session = FakeSoakSession()
    failing_runner = BrokerShadowSoakRunner(
        failing_session,
        clock=clock,
        limits=BrokerShadowSoakLimits(max_cycles=2),
        resource_probe=StableResourceProbe(clock),
    )
    failing_runner.start()
    shutdown_failing = BrokerShadowTemporalSoakScenario(
        "shutdown-failure",
        RaisingShutdownTemporalSoakRunner(
            failing_runner,
            BrokerShadowTemporalSoakPlan(
                duration_seconds=1.0,
                minimum_cycles=1,
                maximum_cycles=2,
            ),
            clock=clock,
        ),
    )
    healthy_session = FakeSoakSession()
    healthy = BrokerShadowTemporalSoakScenario(
        "healthy-after-shutdown-failure",
        BrokerShadowTemporalSoakRunner(
            BrokerShadowSoakRunner(
                healthy_session,
                clock=clock,
                limits=BrokerShadowSoakLimits(max_cycles=2),
                resource_probe=StableResourceProbe(clock),
            ),
            BrokerShadowTemporalSoakPlan(
                duration_seconds=1.0,
                minimum_cycles=1,
                maximum_cycles=2,
            ),
            clock=clock,
            after_cycle=lambda _snapshot: clock.advance(1.0),
        ),
    )

    report = BrokerShadowTemporalSoakMatrixRunner(
        (shutdown_failing, healthy),
        clock=clock,
    ).run()
    serialized = json.dumps(report.to_payload())

    assert report.outcome is BrokerShadowTemporalSoakOutcome.FAILED
    assert report.reason_code == ("BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_SHUTDOWN_FAILED")
    assert report.results[1].outcome is BrokerShadowTemporalSoakOutcome.PASSED
    assert failing_session.state is ShadowServiceState.STOPPED
    assert "raw shutdown detail should not leak" not in serialized


def test_temporal_soak_matrix_rejects_invalid_or_unbounded_scenarios() -> None:
    clock = FakeClock()
    temporal = BrokerShadowTemporalSoakRunner(
        BrokerShadowSoakRunner(
            FakeSoakSession(),
            clock=clock,
            limits=BrokerShadowSoakLimits(max_cycles=1),
            resource_probe=StableResourceProbe(clock),
        ),
        BrokerShadowTemporalSoakPlan(
            duration_seconds=1.0,
            minimum_cycles=1,
            maximum_cycles=1,
        ),
        clock=clock,
    )
    scenario = BrokerShadowTemporalSoakScenario("baseline", temporal)
    second_scenario = BrokerShadowTemporalSoakScenario("second", temporal)

    with pytest.raises(ValueError, match="scenario id"):
        BrokerShadowTemporalSoakScenario("invalid scenario", temporal)
    with pytest.raises(ValueError, match="at least one"):
        BrokerShadowTemporalSoakMatrixRunner(())
    with pytest.raises(ValueError, match="unique"):
        BrokerShadowTemporalSoakMatrixRunner((scenario, scenario))
    with pytest.raises(ValueError, match="limit"):
        BrokerShadowTemporalSoakMatrixRunner((scenario,), maximum_scenarios=0)
    with pytest.raises(ValueError, match="exceeds"):
        BrokerShadowTemporalSoakMatrixRunner(
            (scenario, second_scenario),
            maximum_scenarios=1,
        )
