from __future__ import annotations

import ctypes
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from apps.core.broker_shadow_session import BrokerShadowSessionSnapshot
from apps.core.shadow_host import ProcessResourceSample, ResourceProbe, SystemResourceProbe
from apps.core.shadow_runtime import ShadowServiceState
from packages.market_pipeline import LiveAggregationResult, MonotonicClock, SystemMonotonicClock
from packages.observability import EventSink, NullEventSink, atomic_write_json


class BrokerShadowSoakState(StrEnum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"


class BrokerShadowTemporalSoakOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class BrokerShadowSoakSession(Protocol):
    @property
    def state(self) -> ShadowServiceState: ...

    def snapshot(self) -> BrokerShadowSessionSnapshot: ...

    def start(self) -> bool: ...

    def poll_once(self, *, timeout: float) -> LiveAggregationResult | None: ...

    def recover(self) -> bool: ...

    def shutdown(self) -> None: ...


@dataclass(frozen=True, slots=True)
class BrokerShadowSoakLimits:
    max_cycles: int
    poll_timeout_seconds: float = 0.05
    max_recoveries: int = 3
    max_core_rss_bytes: int | None = None
    max_child_rss_bytes: int | None = None
    max_live_dispatch_lag_ms: int | None = None

    def __post_init__(self) -> None:
        if self.max_cycles <= 0:
            raise ValueError("broker shadow soak cycle limit must be positive")
        if self.poll_timeout_seconds <= 0:
            raise ValueError("broker shadow soak poll timeout must be positive")
        if self.max_recoveries < 0:
            raise ValueError("broker shadow soak recovery limit cannot be negative")
        for value in (
            self.max_core_rss_bytes,
            self.max_child_rss_bytes,
            self.max_live_dispatch_lag_ms,
        ):
            if value is not None and value <= 0:
                raise ValueError("broker shadow soak resource limits must be positive")


@dataclass(frozen=True, slots=True)
class ChildProcessResourceSample:
    pid: int | None
    alive: bool
    rss_bytes: int | None

    def __post_init__(self) -> None:
        if self.pid is not None and self.pid <= 0:
            raise ValueError("child process pid must be positive")
        if self.rss_bytes is not None and self.rss_bytes < 0:
            raise ValueError("child process RSS cannot be negative")
        if not self.alive and self.rss_bytes is not None:
            raise ValueError("stopped child process cannot report RSS")


class ChildProcessProbe(Protocol):
    def sample(self) -> ChildProcessResourceSample: ...


ProcessProvider = Callable[[], subprocess.Popen[bytes] | None]


class PopenChildProcessProbe:
    def __init__(self, process_provider: ProcessProvider) -> None:
        self._process_provider = process_provider

    def sample(self) -> ChildProcessResourceSample:
        process = self._process_provider()
        if process is None:
            return ChildProcessResourceSample(pid=None, alive=False, rss_bytes=None)
        pid = process.pid
        if process.poll() is not None:
            return ChildProcessResourceSample(pid=pid, alive=False, rss_bytes=None)
        return ChildProcessResourceSample(
            pid=pid,
            alive=True,
            rss_bytes=_process_rss_bytes(process),
        )


class NoChildProcessProbe:
    def sample(self) -> ChildProcessResourceSample:
        return ChildProcessResourceSample(pid=None, alive=False, rss_bytes=None)


@dataclass(frozen=True, slots=True)
class BrokerShadowSoakResourceSample:
    observed_monotonic: float
    core_cpu_seconds: float
    core_rss_bytes: int | None
    child_pid: int | None
    child_process_alive: bool
    child_rss_bytes: int | None

    def __post_init__(self) -> None:
        if self.observed_monotonic < 0 or self.core_cpu_seconds < 0:
            raise ValueError("broker shadow soak resource values cannot be negative")
        for value in (self.core_rss_bytes, self.child_rss_bytes):
            if value is not None and value < 0:
                raise ValueError("broker shadow soak RSS values cannot be negative")


@dataclass(frozen=True, slots=True)
class BrokerShadowSoakSnapshot:
    state: BrokerShadowSoakState
    reason_code: str | None
    cycles: int
    poll_attempts: int
    poll_failures: int
    recovery_attempts: int
    latest_resources: BrokerShadowSoakResourceSample | None
    maximum_core_rss_bytes: int | None
    maximum_child_rss_bytes: int | None
    maximum_live_dispatch_lag_ms: int
    session: BrokerShadowSessionSnapshot


@dataclass(frozen=True, slots=True)
class BrokerShadowTemporalSoakPlan:
    duration_seconds: float
    minimum_cycles: int
    maximum_cycles: int
    sample_every_cycles: int = 1
    max_samples: int = 256
    max_poll_failures: int = 0
    max_recovery_attempts: int | None = None
    require_no_degraded_final_state: bool = True

    def __post_init__(self) -> None:
        if self.duration_seconds <= 0:
            raise ValueError("broker shadow temporal soak duration must be positive")
        if self.minimum_cycles <= 0:
            raise ValueError("broker shadow temporal soak minimum cycles must be positive")
        if self.maximum_cycles < self.minimum_cycles:
            raise ValueError("broker shadow temporal soak maximum cycles must cover minimum cycles")
        if self.sample_every_cycles <= 0 or self.max_samples <= 0:
            raise ValueError("broker shadow temporal soak sample limits must be positive")
        if self.max_poll_failures < 0:
            raise ValueError("broker shadow temporal soak poll failure limit cannot be negative")
        if self.max_recovery_attempts is not None and self.max_recovery_attempts < 0:
            raise ValueError("broker shadow temporal soak recovery limit cannot be negative")


@dataclass(frozen=True, slots=True)
class BrokerShadowTemporalSoakSample:
    sample_index: int
    captured_monotonic: float
    runner_state: BrokerShadowSoakState
    reason_code: str | None
    cycles: int
    poll_attempts: int
    poll_failures: int
    recovery_attempts: int
    core_rss_bytes: int | None
    child_pid: int | None
    child_process_alive: bool
    child_rss_bytes: int | None
    maximum_live_dispatch_lag_ms: int
    worker_health: str
    registered_series: int
    subscribed_series: int

    def to_payload(self) -> dict[str, object]:
        return {
            "sample_index": self.sample_index,
            "captured_monotonic": self.captured_monotonic,
            "runner_state": self.runner_state.value,
            "reason_code": self.reason_code,
            "cycles": self.cycles,
            "poll_attempts": self.poll_attempts,
            "poll_failures": self.poll_failures,
            "recovery_attempts": self.recovery_attempts,
            "core_rss_bytes": self.core_rss_bytes,
            "child_pid": self.child_pid,
            "child_process_alive": self.child_process_alive,
            "child_rss_bytes": self.child_rss_bytes,
            "maximum_live_dispatch_lag_ms": self.maximum_live_dispatch_lag_ms,
            "worker_health": self.worker_health,
            "registered_series": self.registered_series,
            "subscribed_series": self.subscribed_series,
        }


@dataclass(frozen=True, slots=True)
class BrokerShadowTemporalSoakReport:
    schema_version: int
    outcome: BrokerShadowTemporalSoakOutcome
    reason_code: str | None
    started_monotonic: float
    ended_monotonic: float
    duration_reached: bool
    dropped_sample_count: int
    plan: BrokerShadowTemporalSoakPlan
    final_snapshot: BrokerShadowSoakSnapshot
    samples: tuple[BrokerShadowTemporalSoakSample, ...]
    shutdown_snapshot: BrokerShadowSoakSnapshot

    @property
    def elapsed_monotonic_seconds(self) -> float:
        return max(0.0, self.ended_monotonic - self.started_monotonic)

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code,
            "started_monotonic": self.started_monotonic,
            "ended_monotonic": self.ended_monotonic,
            "elapsed_monotonic_seconds": self.elapsed_monotonic_seconds,
            "duration_reached": self.duration_reached,
            "dropped_sample_count": self.dropped_sample_count,
            "plan": {
                "duration_seconds": self.plan.duration_seconds,
                "minimum_cycles": self.plan.minimum_cycles,
                "maximum_cycles": self.plan.maximum_cycles,
                "sample_every_cycles": self.plan.sample_every_cycles,
                "max_samples": self.plan.max_samples,
                "max_poll_failures": self.plan.max_poll_failures,
                "max_recovery_attempts": self.plan.max_recovery_attempts,
                "require_no_degraded_final_state": self.plan.require_no_degraded_final_state,
            },
            "final_snapshot": _soak_snapshot_payload(self.final_snapshot),
            "shutdown_snapshot": _soak_snapshot_payload(self.shutdown_snapshot),
            "samples": [sample.to_payload() for sample in self.samples],
        }

    def write_json(self, path: Path) -> None:
        atomic_write_json(path, self.to_payload())


CycleHook = Callable[[int, BrokerShadowSoakSession], None]


class BrokerShadowSoakRunner:
    """Bounded, caller-driven soak owner for a broker-level read-only shadow session."""

    def __init__(
        self,
        session: BrokerShadowSoakSession,
        *,
        clock: MonotonicClock | None = None,
        limits: BrokerShadowSoakLimits | None = None,
        resource_probe: ResourceProbe | None = None,
        child_process_probe: ChildProcessProbe | None = None,
        before_cycle: CycleHook | None = None,
        events: EventSink | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or SystemMonotonicClock()
        self._limits = limits or BrokerShadowSoakLimits(max_cycles=1_000)
        self._resource_probe = resource_probe or SystemResourceProbe(self._clock)
        self._child_process_probe = child_process_probe or NoChildProcessProbe()
        self._before_cycle = before_cycle
        self._events = events or NullEventSink()
        self._state = BrokerShadowSoakState.STOPPED
        self._reason_code: str | None = None
        self._cycles = 0
        self._poll_attempts = 0
        self._poll_failures = 0
        self._recovery_attempts = 0
        self._latest_resources: BrokerShadowSoakResourceSample | None = None
        self._maximum_core_rss: int | None = None
        self._maximum_child_rss: int | None = None
        self._maximum_lag_ms = 0

    @property
    def state(self) -> BrokerShadowSoakState:
        return self._state

    def start(self) -> BrokerShadowSoakSnapshot:
        if self._state is not BrokerShadowSoakState.STOPPED:
            raise RuntimeError("BROKER_SHADOW_SOAK_ALREADY_STARTED")
        self._events.emit("broker_shadow_soak_starting")
        ready = self._session.start()
        self._state = BrokerShadowSoakState.RUNNING if ready else BrokerShadowSoakState.DEGRADED
        self._sample_resources()
        reason = self._resource_limit_reason()
        if reason is not None:
            self._exhaust(reason)
        else:
            self._events.emit("broker_shadow_soak_started", ready=ready)
        return self.snapshot()

    def run_cycle(self) -> BrokerShadowSoakSnapshot:
        if self._state is BrokerShadowSoakState.STOPPED:
            self.start()
        if self._state in {
            BrokerShadowSoakState.COMPLETED,
            BrokerShadowSoakState.FAILED,
            BrokerShadowSoakState.RESOURCE_EXHAUSTED,
        }:
            return self.snapshot()
        if self._state not in {BrokerShadowSoakState.RUNNING, BrokerShadowSoakState.DEGRADED}:
            raise RuntimeError("BROKER_SHADOW_SOAK_NOT_RUNNABLE")
        cycle_number = self._cycles + 1
        if self._before_cycle is not None:
            self._before_cycle(cycle_number, self._session)
        self._cycles = cycle_number
        self._sample_resources()
        reason = self._resource_limit_reason()
        if reason is not None:
            self._exhaust(reason)
            return self.snapshot()
        self._run_session_action()
        self._sample_resources()
        reason = self._resource_limit_reason()
        if reason is not None:
            self._exhaust(reason)
        else:
            self._derive_state()
        return self.snapshot()

    def run_until_complete(self) -> BrokerShadowSoakSnapshot:
        while self._cycles < self._limits.max_cycles and self._state not in {
            BrokerShadowSoakState.COMPLETED,
            BrokerShadowSoakState.FAILED,
            BrokerShadowSoakState.RESOURCE_EXHAUSTED,
        }:
            self.run_cycle()
        if self._state in {BrokerShadowSoakState.RUNNING, BrokerShadowSoakState.DEGRADED}:
            if self._session.state is ShadowServiceState.RUNNING:
                self._state = BrokerShadowSoakState.COMPLETED
                self._reason_code = None
                self._events.emit(
                    "broker_shadow_soak_completed",
                    cycles=self._cycles,
                    recovery_attempts=self._recovery_attempts,
                )
            else:
                self._fail("BROKER_SHADOW_SOAK_SESSION_NOT_READY_AT_COMPLETION")
        return self.snapshot()

    def shutdown(self) -> BrokerShadowSoakSnapshot:
        if self._state is not BrokerShadowSoakState.STOPPED:
            self._session.shutdown()
            self._state = BrokerShadowSoakState.STOPPED
            self._reason_code = None
            self._events.emit("broker_shadow_soak_stopped")
        return self.snapshot()

    def snapshot(self) -> BrokerShadowSoakSnapshot:
        return BrokerShadowSoakSnapshot(
            state=self._state,
            reason_code=self._reason_code,
            cycles=self._cycles,
            poll_attempts=self._poll_attempts,
            poll_failures=self._poll_failures,
            recovery_attempts=self._recovery_attempts,
            latest_resources=self._latest_resources,
            maximum_core_rss_bytes=self._maximum_core_rss,
            maximum_child_rss_bytes=self._maximum_child_rss,
            maximum_live_dispatch_lag_ms=self._maximum_lag_ms,
            session=self._session.snapshot(),
        )

    def _run_session_action(self) -> None:
        if self._session.state is ShadowServiceState.RUNNING:
            self._poll_attempts += 1
            try:
                self._session.poll_once(timeout=self._limits.poll_timeout_seconds)
            except Exception:
                self._poll_failures += 1
                self._events.emit(
                    "broker_shadow_soak_poll_failed",
                    poll_attempts=self._poll_attempts,
                )
        elif self._session.state is ShadowServiceState.RECOVERING:
            if self._recovery_attempts >= self._limits.max_recoveries:
                self._fail("BROKER_SHADOW_SOAK_RECOVERY_LIMIT_EXCEEDED")
                return
            self._recovery_attempts += 1
            try:
                ready = self._session.recover()
            except Exception:
                self._fail("BROKER_SHADOW_SOAK_RECOVERY_FAILED")
                return
            if not ready:
                self._events.emit(
                    "broker_shadow_soak_recovery_incomplete",
                    recovery_attempts=self._recovery_attempts,
                )
        elif self._session.state is ShadowServiceState.FAILED:
            self._fail("BROKER_SHADOW_SOAK_SESSION_FAILED")
        elif self._session.state is ShadowServiceState.STOPPED:
            self._fail("BROKER_SHADOW_SOAK_SESSION_STOPPED")

    def _sample_resources(self) -> BrokerShadowSoakResourceSample:
        core = self._resource_probe.sample()
        child = self._child_process_probe.sample()
        sample = BrokerShadowSoakResourceSample(
            observed_monotonic=core.observed_monotonic,
            core_cpu_seconds=core.process_cpu_seconds,
            core_rss_bytes=core.rss_bytes,
            child_pid=child.pid,
            child_process_alive=child.alive,
            child_rss_bytes=child.rss_bytes,
        )
        self._latest_resources = sample
        self._record_maximums(core, child)
        return sample

    def _record_maximums(
        self,
        core: ProcessResourceSample,
        child: ChildProcessResourceSample,
    ) -> None:
        if core.rss_bytes is not None:
            self._maximum_core_rss = max(self._maximum_core_rss or 0, core.rss_bytes)
        if child.rss_bytes is not None:
            self._maximum_child_rss = max(self._maximum_child_rss or 0, child.rss_bytes)
        session = self._session.snapshot()
        self._maximum_lag_ms = max(
            self._maximum_lag_ms,
            *(series.live_dispatch_lag_ms_max for series in session.series),
        )

    def _resource_limit_reason(self) -> str | None:
        sample = self._latest_resources
        if sample is None:
            return None
        if self._limits.max_core_rss_bytes is not None:
            if sample.core_rss_bytes is None:
                return "BROKER_SHADOW_SOAK_CORE_RSS_UNAVAILABLE"
            if sample.core_rss_bytes > self._limits.max_core_rss_bytes:
                return "BROKER_SHADOW_SOAK_CORE_RSS_LIMIT_EXCEEDED"
        if self._limits.max_child_rss_bytes is not None and sample.child_process_alive:
            if sample.child_rss_bytes is None:
                return "BROKER_SHADOW_SOAK_CHILD_RSS_UNAVAILABLE"
            if sample.child_rss_bytes > self._limits.max_child_rss_bytes:
                return "BROKER_SHADOW_SOAK_CHILD_RSS_LIMIT_EXCEEDED"
        if (
            self._limits.max_live_dispatch_lag_ms is not None
            and self._maximum_lag_ms > self._limits.max_live_dispatch_lag_ms
        ):
            return "BROKER_SHADOW_SOAK_LAG_LIMIT_EXCEEDED"
        return None

    def _derive_state(self) -> None:
        if self._state in {
            BrokerShadowSoakState.FAILED,
            BrokerShadowSoakState.RESOURCE_EXHAUSTED,
        }:
            return
        if self._session.state is ShadowServiceState.RUNNING:
            self._state = BrokerShadowSoakState.RUNNING
            self._reason_code = None
            return
        self._state = BrokerShadowSoakState.DEGRADED
        self._reason_code = "BROKER_SHADOW_SOAK_SESSION_DEGRADED"

    def _fail(self, reason_code: str) -> None:
        self._reason_code = reason_code
        self._state = BrokerShadowSoakState.FAILED
        self._session.shutdown()
        self._events.emit("broker_shadow_soak_failed", reason_code=reason_code)

    def _exhaust(self, reason_code: str) -> None:
        self._reason_code = reason_code
        self._state = BrokerShadowSoakState.RESOURCE_EXHAUSTED
        self._session.shutdown()
        self._events.emit("broker_shadow_soak_resource_exhausted", reason_code=reason_code)


AfterTemporalCycle = Callable[[BrokerShadowSoakSnapshot], None]


class BrokerShadowTemporalSoakRunner:
    """Runs a finite monotonic time window over an existing broker shadow soak runner."""

    _SCHEMA_VERSION = 1

    def __init__(
        self,
        runner: BrokerShadowSoakRunner,
        plan: BrokerShadowTemporalSoakPlan,
        *,
        clock: MonotonicClock | None = None,
        after_cycle: AfterTemporalCycle | None = None,
        events: EventSink | None = None,
    ) -> None:
        self._runner = runner
        self._plan = plan
        self._clock = clock or SystemMonotonicClock()
        self._after_cycle = after_cycle
        self._events = events or NullEventSink()
        self._samples: list[BrokerShadowTemporalSoakSample] = []
        self._sample_counter = 0
        self._dropped_samples = 0

    def run(self) -> BrokerShadowTemporalSoakReport:
        started = self._clock.now()
        self._events.emit(
            "broker_shadow_temporal_soak_started",
            duration_ms=int(self._plan.duration_seconds * 1_000),
            minimum_cycles=self._plan.minimum_cycles,
        )
        snapshot = self._runner.snapshot()
        while not self._window_reached(started, snapshot):
            if snapshot.cycles >= self._plan.maximum_cycles:
                break
            snapshot = self._runner.run_cycle()
            self._maybe_record_sample(snapshot)
            if self._after_cycle is not None:
                self._after_cycle(snapshot)
            if snapshot.state in {
                BrokerShadowSoakState.COMPLETED,
                BrokerShadowSoakState.FAILED,
                BrokerShadowSoakState.RESOURCE_EXHAUSTED,
            }:
                break
        ended = self._clock.now()
        final_snapshot = self._runner.snapshot()
        self._maybe_record_sample(final_snapshot, force=True)
        outcome, reason = self._evaluate(started, ended, final_snapshot)
        shutdown_snapshot = self._runner.shutdown()
        report = BrokerShadowTemporalSoakReport(
            schema_version=self._SCHEMA_VERSION,
            outcome=outcome,
            reason_code=reason,
            started_monotonic=started,
            ended_monotonic=ended,
            duration_reached=ended - started >= self._plan.duration_seconds,
            dropped_sample_count=self._dropped_samples,
            plan=self._plan,
            final_snapshot=final_snapshot,
            samples=tuple(self._samples),
            shutdown_snapshot=shutdown_snapshot,
        )
        self._events.emit(
            "broker_shadow_temporal_soak_finished",
            outcome=outcome.value,
            reason_code=reason,
            cycles=final_snapshot.cycles,
            poll_failures=final_snapshot.poll_failures,
            recovery_attempts=final_snapshot.recovery_attempts,
        )
        return report

    def shutdown(self) -> BrokerShadowSoakSnapshot:
        """Stops the owned read-only runner after an interrupted scenario."""
        return self._runner.shutdown()

    def _window_reached(
        self,
        started_monotonic: float,
        snapshot: BrokerShadowSoakSnapshot,
    ) -> bool:
        if snapshot.cycles < self._plan.minimum_cycles:
            return False
        return self._clock.now() - started_monotonic >= self._plan.duration_seconds

    def _maybe_record_sample(
        self,
        snapshot: BrokerShadowSoakSnapshot,
        *,
        force: bool = False,
    ) -> None:
        if not force and snapshot.cycles % self._plan.sample_every_cycles != 0:
            return
        self._sample_counter += 1
        resources = snapshot.latest_resources
        sample = BrokerShadowTemporalSoakSample(
            sample_index=self._sample_counter,
            captured_monotonic=self._clock.now(),
            runner_state=snapshot.state,
            reason_code=snapshot.reason_code,
            cycles=snapshot.cycles,
            poll_attempts=snapshot.poll_attempts,
            poll_failures=snapshot.poll_failures,
            recovery_attempts=snapshot.recovery_attempts,
            core_rss_bytes=None if resources is None else resources.core_rss_bytes,
            child_pid=None if resources is None else resources.child_pid,
            child_process_alive=False if resources is None else resources.child_process_alive,
            child_rss_bytes=None if resources is None else resources.child_rss_bytes,
            maximum_live_dispatch_lag_ms=snapshot.maximum_live_dispatch_lag_ms,
            worker_health=snapshot.session.worker_health.value,
            registered_series=snapshot.session.registered_series,
            subscribed_series=snapshot.session.subscribed_series,
        )
        if len(self._samples) >= self._plan.max_samples:
            self._samples.pop(0)
            self._dropped_samples += 1
        self._samples.append(sample)

    def _evaluate(
        self,
        started_monotonic: float,
        ended_monotonic: float,
        snapshot: BrokerShadowSoakSnapshot,
    ) -> tuple[BrokerShadowTemporalSoakOutcome, str | None]:
        if ended_monotonic - started_monotonic < self._plan.duration_seconds:
            return (
                BrokerShadowTemporalSoakOutcome.FAILED,
                "BROKER_SHADOW_TEMPORAL_SOAK_DURATION_NOT_REACHED",
            )
        if snapshot.cycles < self._plan.minimum_cycles:
            return (
                BrokerShadowTemporalSoakOutcome.FAILED,
                "BROKER_SHADOW_TEMPORAL_SOAK_MINIMUM_CYCLES_NOT_REACHED",
            )
        if snapshot.state in {
            BrokerShadowSoakState.FAILED,
            BrokerShadowSoakState.RESOURCE_EXHAUSTED,
        }:
            return (
                BrokerShadowTemporalSoakOutcome.FAILED,
                snapshot.reason_code or "BROKER_SHADOW_TEMPORAL_SOAK_RUNNER_FAILED",
            )
        if (
            self._plan.require_no_degraded_final_state
            and snapshot.state is BrokerShadowSoakState.DEGRADED
        ):
            return (
                BrokerShadowTemporalSoakOutcome.FAILED,
                "BROKER_SHADOW_TEMPORAL_SOAK_FINAL_STATE_DEGRADED",
            )
        if snapshot.poll_failures > self._plan.max_poll_failures:
            return (
                BrokerShadowTemporalSoakOutcome.FAILED,
                "BROKER_SHADOW_TEMPORAL_SOAK_POLL_FAILURE_LIMIT_EXCEEDED",
            )
        if (
            self._plan.max_recovery_attempts is not None
            and snapshot.recovery_attempts > self._plan.max_recovery_attempts
        ):
            return (
                BrokerShadowTemporalSoakOutcome.FAILED,
                "BROKER_SHADOW_TEMPORAL_SOAK_RECOVERY_LIMIT_EXCEEDED",
            )
        return (BrokerShadowTemporalSoakOutcome.PASSED, None)


@dataclass(frozen=True, slots=True)
class BrokerShadowTemporalSoakScenario:
    scenario_id: str
    runner: BrokerShadowTemporalSoakRunner

    def __post_init__(self) -> None:
        if not self.scenario_id or len(self.scenario_id) > 64:
            raise ValueError(
                "broker shadow temporal soak scenario id must contain 1 to 64 characters"
            )
        if not all(
            character.isascii() and (character.isalnum() or character in "._-")
            for character in self.scenario_id
        ):
            raise ValueError("broker shadow temporal soak scenario id contains invalid characters")


@dataclass(frozen=True, slots=True)
class BrokerShadowTemporalSoakScenarioResult:
    scenario_id: str
    outcome: BrokerShadowTemporalSoakOutcome
    reason_code: str | None
    report: BrokerShadowTemporalSoakReport | None

    def to_payload(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code,
            "report": None if self.report is None else self.report.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class BrokerShadowTemporalSoakMatrixReport:
    schema_version: int
    outcome: BrokerShadowTemporalSoakOutcome
    reason_code: str | None
    started_monotonic: float
    ended_monotonic: float
    maximum_scenarios: int
    results: tuple[BrokerShadowTemporalSoakScenarioResult, ...]

    @property
    def elapsed_monotonic_seconds(self) -> float:
        return max(0.0, self.ended_monotonic - self.started_monotonic)

    @property
    def passed_scenario_count(self) -> int:
        return sum(
            result.outcome is BrokerShadowTemporalSoakOutcome.PASSED for result in self.results
        )

    @property
    def failed_scenario_count(self) -> int:
        return len(self.results) - self.passed_scenario_count

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code,
            "started_monotonic": self.started_monotonic,
            "ended_monotonic": self.ended_monotonic,
            "elapsed_monotonic_seconds": self.elapsed_monotonic_seconds,
            "maximum_scenarios": self.maximum_scenarios,
            "scenario_count": len(self.results),
            "passed_scenario_count": self.passed_scenario_count,
            "failed_scenario_count": self.failed_scenario_count,
            "results": [result.to_payload() for result in self.results],
        }

    def write_json(self, path: Path) -> None:
        atomic_write_json(path, self.to_payload())


class BrokerShadowTemporalSoakMatrixRunner:
    """Runs a bounded local matrix of redacted read-only temporal soak scenarios."""

    _SCHEMA_VERSION = 1

    def __init__(
        self,
        scenarios: tuple[BrokerShadowTemporalSoakScenario, ...],
        *,
        maximum_scenarios: int = 16,
        clock: MonotonicClock | None = None,
        events: EventSink | None = None,
    ) -> None:
        if maximum_scenarios <= 0:
            raise ValueError("broker shadow temporal soak matrix limit must be positive")
        if not scenarios:
            raise ValueError(
                "broker shadow temporal soak matrix must contain at least one scenario"
            )
        if len(scenarios) > maximum_scenarios:
            raise ValueError("broker shadow temporal soak matrix exceeds scenario limit")
        scenario_ids = tuple(scenario.scenario_id for scenario in scenarios)
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("broker shadow temporal soak matrix scenario ids must be unique")
        self._scenarios = scenarios
        self._maximum_scenarios = maximum_scenarios
        self._clock = clock or SystemMonotonicClock()
        self._events = events or NullEventSink()

    def run(self) -> BrokerShadowTemporalSoakMatrixReport:
        started = self._clock.now()
        self._events.emit(
            "broker_shadow_temporal_soak_matrix_started",
            scenario_count=len(self._scenarios),
            maximum_scenarios=self._maximum_scenarios,
        )
        results: list[BrokerShadowTemporalSoakScenarioResult] = []
        for scenario in self._scenarios:
            result = self._run_scenario(scenario)
            results.append(result)
        ended = self._clock.now()
        outcome, reason_code = self._evaluate(tuple(results))
        report = BrokerShadowTemporalSoakMatrixReport(
            schema_version=self._SCHEMA_VERSION,
            outcome=outcome,
            reason_code=reason_code,
            started_monotonic=started,
            ended_monotonic=ended,
            maximum_scenarios=self._maximum_scenarios,
            results=tuple(results),
        )
        self._events.emit(
            "broker_shadow_temporal_soak_matrix_finished",
            outcome=outcome.value,
            reason_code=reason_code,
            passed_scenarios=report.passed_scenario_count,
            failed_scenarios=report.failed_scenario_count,
        )
        return report

    def _run_scenario(
        self,
        scenario: BrokerShadowTemporalSoakScenario,
    ) -> BrokerShadowTemporalSoakScenarioResult:
        self._events.emit(
            "broker_shadow_temporal_soak_matrix_scenario_started",
            scenario_id=scenario.scenario_id,
        )
        try:
            report = scenario.runner.run()
        except Exception:
            reason_code = "BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_RAISED"
            try:
                scenario.runner.shutdown()
            except Exception:
                reason_code = "BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_SHUTDOWN_FAILED"
            result = BrokerShadowTemporalSoakScenarioResult(
                scenario_id=scenario.scenario_id,
                outcome=BrokerShadowTemporalSoakOutcome.FAILED,
                reason_code=reason_code,
                report=None,
            )
        else:
            result = BrokerShadowTemporalSoakScenarioResult(
                scenario_id=scenario.scenario_id,
                outcome=report.outcome,
                reason_code=report.reason_code,
                report=report,
            )
        self._events.emit(
            "broker_shadow_temporal_soak_matrix_scenario_finished",
            scenario_id=scenario.scenario_id,
            outcome=result.outcome.value,
            reason_code=result.reason_code,
        )
        return result

    @staticmethod
    def _evaluate(
        results: tuple[BrokerShadowTemporalSoakScenarioResult, ...],
    ) -> tuple[BrokerShadowTemporalSoakOutcome, str | None]:
        if all(result.outcome is BrokerShadowTemporalSoakOutcome.PASSED for result in results):
            return (BrokerShadowTemporalSoakOutcome.PASSED, None)
        if any(
            result.reason_code == "BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_SHUTDOWN_FAILED"
            for result in results
        ):
            return (
                BrokerShadowTemporalSoakOutcome.FAILED,
                "BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_SHUTDOWN_FAILED",
            )
        if any(result.report is None for result in results):
            return (
                BrokerShadowTemporalSoakOutcome.FAILED,
                "BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_RAISED",
            )
        return (
            BrokerShadowTemporalSoakOutcome.FAILED,
            "BROKER_SHADOW_TEMPORAL_SOAK_MATRIX_SCENARIO_FAILED",
        )


def _soak_snapshot_payload(snapshot: BrokerShadowSoakSnapshot) -> dict[str, object]:
    return {
        "state": snapshot.state.value,
        "reason_code": snapshot.reason_code,
        "cycles": snapshot.cycles,
        "poll_attempts": snapshot.poll_attempts,
        "poll_failures": snapshot.poll_failures,
        "recovery_attempts": snapshot.recovery_attempts,
        "latest_resources": _resource_sample_payload(snapshot.latest_resources),
        "maximum_core_rss_bytes": snapshot.maximum_core_rss_bytes,
        "maximum_child_rss_bytes": snapshot.maximum_child_rss_bytes,
        "maximum_live_dispatch_lag_ms": snapshot.maximum_live_dispatch_lag_ms,
        "session": _session_snapshot_payload(snapshot.session),
    }


def _resource_sample_payload(
    sample: BrokerShadowSoakResourceSample | None,
) -> dict[str, object] | None:
    if sample is None:
        return None
    return {
        "observed_monotonic": sample.observed_monotonic,
        "core_cpu_seconds": sample.core_cpu_seconds,
        "core_rss_bytes": sample.core_rss_bytes,
        "child_pid": sample.child_pid,
        "child_process_alive": sample.child_process_alive,
        "child_rss_bytes": sample.child_rss_bytes,
    }


def _session_snapshot_payload(snapshot: BrokerShadowSessionSnapshot) -> dict[str, object]:
    router = snapshot.router
    return {
        "broker": snapshot.broker.value,
        "state": snapshot.state.value,
        "worker_health": snapshot.worker_health.value,
        "registered_series": snapshot.registered_series,
        "subscribed_series": snapshot.subscribed_series,
        "start_attempts": snapshot.start_attempts,
        "recovery_attempts": snapshot.recovery_attempts,
        "poll_count": snapshot.poll_count,
        "poll_failures": snapshot.poll_failures,
        "elapsed_monotonic_seconds": snapshot.elapsed_monotonic_seconds,
        "router": None
        if router is None
        else {
            "broker": router.broker.value,
            "registered_series": router.registered_series,
            "active_subscriptions": router.active_subscriptions,
            "source_ticks_received": router.source_ticks_received,
            "source_timeouts": router.source_timeouts,
            "unroutable_ticks": router.unroutable_ticks,
            "backpressure_count": router.backpressure_count,
        },
        "series": [
            {
                "series_id": item.series_id.key,
                "subscribed": item.subscribed,
                "poll_count": item.poll_count,
                "poll_failures": item.poll_failures,
                "live_dispatch_lag_ms_max": item.live_dispatch_lag_ms_max,
            }
            for item in snapshot.series
        ],
    }


def _process_rss_bytes(process: subprocess.Popen[bytes]) -> int | None:
    if os.name == "nt":
        raw_handle = getattr(process, "_handle", None)
        if raw_handle is None:
            return None
        try:
            return _windows_process_rss_bytes(int(raw_handle))
        except (OSError, TypeError, ValueError):
            return None
    return _posix_process_rss_bytes(process.pid)


def _posix_process_rss_bytes(pid: int) -> int | None:
    statm_path = f"/proc/{pid}/statm"
    try:
        with open(statm_path, encoding="ascii") as handle:
            parts = handle.read().split()
        if len(parts) < 2:
            return None
        sysconf = getattr(os, "sysconf", None)
        if not callable(sysconf):
            return None
        page_size = sysconf("SC_PAGE_SIZE")
        if not isinstance(page_size, int) or page_size <= 0:
            return None
        return int(parts[1]) * page_size
    except (OSError, ValueError):
        return None


def _windows_process_rss_bytes(process_handle: int) -> int | None:
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("page_fault_count", ctypes.c_ulong),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
        ]

    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        get_process_memory_info = psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        )
        get_process_memory_info.restype = ctypes.c_int
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not get_process_memory_info(
            ctypes.c_void_p(process_handle),
            ctypes.byref(counters),
            counters.cb,
        ):
            return None
        return int(counters.working_set_size)
    except OSError:
        return None
