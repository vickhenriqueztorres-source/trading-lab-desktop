from __future__ import annotations

import ctypes
import importlib
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from apps.core.shadow_runtime import ShadowServiceSnapshot, ShadowServiceState
from apps.core.worker_supervisor import CircuitState, CrashCircuitBreaker, RestartPolicy
from packages.market_pipeline import LiveAggregationResult, MarketSeriesId, MonotonicClock
from packages.observability import EventSink, NullEventSink


class ShadowHostState(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    STOPPING = "STOPPING"


class HostedShadowService(Protocol):
    @property
    def state(self) -> ShadowServiceState: ...

    def snapshot(self) -> ShadowServiceSnapshot: ...

    def start(self) -> bool: ...

    def poll_once(self, *, timeout: float) -> LiveAggregationResult | None: ...

    def recover(self) -> bool: ...

    def shutdown(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ShadowHostLimits:
    maximum_series: int = 16
    maximum_actions_per_cycle: int = 8
    maximum_poll_timeout_seconds: float = 1.0
    maximum_rss_bytes: int | None = None
    maximum_cpu_seconds_per_cycle: float | None = None
    maximum_live_dispatch_lag_ms: int | None = None

    def __post_init__(self) -> None:
        if self.maximum_series <= 0 or self.maximum_actions_per_cycle <= 0:
            raise ValueError("shadow host series and action limits must be positive")
        if self.maximum_poll_timeout_seconds <= 0:
            raise ValueError("shadow host poll timeout limit must be positive")
        for value in (self.maximum_rss_bytes, self.maximum_live_dispatch_lag_ms):
            if value is not None and value <= 0:
                raise ValueError("shadow host integer resource limits must be positive")
        if (
            self.maximum_cpu_seconds_per_cycle is not None
            and self.maximum_cpu_seconds_per_cycle <= 0
        ):
            raise ValueError("shadow host CPU limit must be positive")


@dataclass(frozen=True, slots=True)
class ProcessResourceSample:
    observed_monotonic: float
    process_cpu_seconds: float
    rss_bytes: int | None

    def __post_init__(self) -> None:
        if self.observed_monotonic < 0 or self.process_cpu_seconds < 0:
            raise ValueError("process resource sample cannot be negative")
        if self.rss_bytes is not None and self.rss_bytes < 0:
            raise ValueError("process RSS cannot be negative")


class ResourceProbe(Protocol):
    def sample(self) -> ProcessResourceSample: ...


class SystemResourceProbe:
    def __init__(self, clock: MonotonicClock) -> None:
        self._clock = clock

    def sample(self) -> ProcessResourceSample:
        return ProcessResourceSample(
            observed_monotonic=self._clock.now(),
            process_cpu_seconds=time.process_time(),
            rss_bytes=_current_rss_bytes(),
        )


@dataclass(frozen=True, slots=True)
class HostedSeriesSnapshot:
    series_id: MarketSeriesId
    service: ShadowServiceSnapshot
    circuit_state: CircuitState
    consecutive_recovery_failures: int
    next_recovery_monotonic: float | None


@dataclass(frozen=True, slots=True)
class ShadowHostSnapshot:
    state: ShadowHostState
    reason_code: str | None
    cycles: int
    actions: int
    poll_failures: int
    recovery_attempts: int
    recovery_failures: int
    shutdown_failures: int
    latest_resources: ProcessResourceSample | None
    maximum_observed_rss_bytes: int | None
    maximum_observed_live_dispatch_lag_ms: int
    series: tuple[HostedSeriesSnapshot, ...]


@dataclass(slots=True)
class _HostedSeries:
    series_id: MarketSeriesId
    service: HostedShadowService
    breaker: CrashCircuitBreaker
    consecutive_recovery_failures: int = 0
    next_recovery_monotonic: float | None = None


class ShadowRuntimeHost:
    """Bounded, fair and caller-driven host for isolated shadow series."""

    def __init__(
        self,
        clock: MonotonicClock,
        *,
        limits: ShadowHostLimits | None = None,
        restart_policy: RestartPolicy | None = None,
        resource_probe: ResourceProbe | None = None,
        jitter: Callable[[float], float] = lambda _ceiling: 0.0,
        events: EventSink | None = None,
    ) -> None:
        self._clock = clock
        self._limits = limits or ShadowHostLimits()
        self._restart_policy = restart_policy or RestartPolicy()
        self._resource_probe = resource_probe or SystemResourceProbe(clock)
        self._jitter = jitter
        self._events = events or NullEventSink()
        self._entries: list[_HostedSeries] = []
        self._by_series: dict[MarketSeriesId, _HostedSeries] = {}
        self._cursor = 0
        self._state = ShadowHostState.STOPPED
        self._reason_code: str | None = None
        self._cycles = 0
        self._actions = 0
        self._poll_failures = 0
        self._recovery_attempts = 0
        self._recovery_failures = 0
        self._shutdown_failures = 0
        self._latest_resources: ProcessResourceSample | None = None
        self._maximum_rss: int | None = None
        self._maximum_lag_ms = 0

    @property
    def state(self) -> ShadowHostState:
        return self._state

    def register(self, series_id: MarketSeriesId, service: HostedShadowService) -> None:
        if self._state is not ShadowHostState.STOPPED:
            raise RuntimeError("SHADOW_HOST_REGISTRATION_CLOSED")
        if series_id in self._by_series:
            raise ValueError("shadow host series is already registered")
        if len(self._entries) >= self._limits.maximum_series:
            raise ValueError("shadow host series limit exceeded")
        entry = _HostedSeries(
            series_id,
            service,
            CrashCircuitBreaker(self._restart_policy, monotonic=self._clock.now),
        )
        self._entries.append(entry)
        self._by_series[series_id] = entry

    def start(self) -> ShadowHostSnapshot:
        if self._state is not ShadowHostState.STOPPED:
            raise RuntimeError("SHADOW_HOST_ALREADY_STARTED")
        if not self._entries:
            raise RuntimeError("SHADOW_HOST_HAS_NO_SERIES")
        self._state = ShadowHostState.STARTING
        self._reason_code = None
        before = self._sample_resources()
        if self._resource_limit_reason(before) is not None:
            self._exhaust_resources(self._resource_limit_reason(before) or "SHADOW_RESOURCE_LIMIT")
            return self.snapshot()
        for entry in self._entries:
            try:
                ready = entry.service.start()
            except Exception:
                ready = False
            if not ready:
                self._schedule_recovery(entry, "SHADOW_START_FAILED")
        after = self._sample_resources()
        if self._cycle_resource_reason(before, after) is not None:
            self._exhaust_resources(
                self._cycle_resource_reason(before, after) or "SHADOW_RESOURCE_LIMIT"
            )
        else:
            self._derive_state()
        self._events.emit("shadow_host_started", series_count=len(self._entries))
        return self.snapshot()

    def run_cycle(
        self,
        *,
        poll_timeout: float,
        maximum_actions: int | None = None,
    ) -> ShadowHostSnapshot:
        if self._state not in {ShadowHostState.RUNNING, ShadowHostState.DEGRADED}:
            raise RuntimeError("SHADOW_HOST_NOT_RUNNABLE")
        if poll_timeout <= 0 or poll_timeout > self._limits.maximum_poll_timeout_seconds:
            raise ValueError("shadow host poll timeout is outside the bounded limit")
        action_limit = (
            self._limits.maximum_actions_per_cycle if maximum_actions is None else maximum_actions
        )
        if action_limit <= 0 or action_limit > self._limits.maximum_actions_per_cycle:
            raise ValueError("shadow host action count is outside the bounded limit")
        before = self._sample_resources()
        reason = self._resource_limit_reason(before)
        if reason is not None:
            self._exhaust_resources(reason)
            return self.snapshot()
        self._cycles += 1
        actions = 0
        visited = 0
        count = len(self._entries)
        while visited < count and actions < action_limit:
            index = (self._cursor + visited) % count
            entry = self._entries[index]
            visited += 1
            if entry.service.state is ShadowServiceState.RUNNING:
                actions += 1
                self._poll(entry, poll_timeout)
            elif self._recovery_due(entry):
                actions += 1
                self._recover(entry)
        self._cursor = (self._cursor + max(1, visited)) % count
        self._actions += actions
        after = self._sample_resources()
        reason = self._cycle_resource_reason(before, after)
        if reason is not None:
            self._exhaust_resources(reason)
        else:
            self._derive_state()
        return self.snapshot()

    def shutdown(self) -> ShadowHostSnapshot:
        if self._state is ShadowHostState.STOPPED:
            return self.snapshot()
        self._state = ShadowHostState.STOPPING
        self._shutdown_all()
        self._state = ShadowHostState.STOPPED
        self._reason_code = None
        self._events.emit("shadow_host_stopped")
        return self.snapshot()

    def snapshot(self) -> ShadowHostSnapshot:
        snapshots = tuple(
            HostedSeriesSnapshot(
                entry.series_id,
                entry.service.snapshot(),
                entry.breaker.state,
                entry.consecutive_recovery_failures,
                entry.next_recovery_monotonic,
            )
            for entry in self._entries
        )
        return ShadowHostSnapshot(
            state=self._state,
            reason_code=self._reason_code,
            cycles=self._cycles,
            actions=self._actions,
            poll_failures=self._poll_failures,
            recovery_attempts=self._recovery_attempts,
            recovery_failures=self._recovery_failures,
            shutdown_failures=self._shutdown_failures,
            latest_resources=self._latest_resources,
            maximum_observed_rss_bytes=self._maximum_rss,
            maximum_observed_live_dispatch_lag_ms=self._maximum_lag_ms,
            series=snapshots,
        )

    def _poll(self, entry: _HostedSeries, timeout: float) -> None:
        try:
            entry.service.poll_once(timeout=timeout)
        except Exception:
            self._poll_failures += 1
        if entry.service.state is not ShadowServiceState.RUNNING:
            self._schedule_recovery(entry, "SHADOW_POLL_FAILED")

    def _recover(self, entry: _HostedSeries) -> None:
        if not entry.breaker.allow_restart():
            entry.next_recovery_monotonic = self._clock.now() + self._restart_policy.open_seconds
            return
        entry.next_recovery_monotonic = None
        self._recovery_attempts += 1
        try:
            ready = entry.service.recover()
        except Exception:
            ready = False
        if ready:
            entry.breaker.record_success()
            entry.consecutive_recovery_failures = 0
            self._events.emit("shadow_host_series_recovered", series_id=entry.series_id.key)
            return
        self._recovery_failures += 1
        self._schedule_recovery(entry, "SHADOW_RECOVERY_FAILED")

    def _schedule_recovery(self, entry: _HostedSeries, reason_code: str) -> None:
        if entry.next_recovery_monotonic is not None:
            return
        entry.breaker.record_crash()
        entry.consecutive_recovery_failures += 1
        now = self._clock.now()
        if entry.breaker.state is CircuitState.OPEN:
            delay = self._restart_policy.open_seconds
            self._events.emit(
                "shadow_host_circuit_opened",
                reason_code=reason_code,
                series_id=entry.series_id.key,
            )
        else:
            base = entry.breaker.next_delay()
            ceiling = base * 0.2
            jitter = self._jitter(ceiling)
            if jitter < 0 or jitter > ceiling:
                raise ValueError("shadow host jitter is outside its ceiling")
            delay = min(self._restart_policy.max_delay_seconds, base + jitter)
        entry.next_recovery_monotonic = now + delay
        self._events.emit(
            "shadow_host_recovery_scheduled",
            reason_code=reason_code,
            series_id=entry.series_id.key,
            delay_ms=int(delay * 1_000),
        )

    def _recovery_due(self, entry: _HostedSeries) -> bool:
        due = entry.next_recovery_monotonic
        return due is not None and self._clock.now() >= due

    def _derive_state(self) -> None:
        if all(entry.service.state is ShadowServiceState.RUNNING for entry in self._entries):
            self._state = ShadowHostState.RUNNING
            self._reason_code = None
        else:
            self._state = ShadowHostState.DEGRADED
            self._reason_code = "SHADOW_SERIES_DEGRADED"

    def _sample_resources(self) -> ProcessResourceSample:
        try:
            sample = self._resource_probe.sample()
        except Exception:
            self._exhaust_resources("SHADOW_RESOURCE_TELEMETRY_FAILED")
            raise
        self._latest_resources = sample
        if sample.rss_bytes is not None:
            self._maximum_rss = max(self._maximum_rss or 0, sample.rss_bytes)
        self._maximum_lag_ms = max(
            self._maximum_lag_ms,
            *(entry.service.snapshot().live_dispatch_lag_ms_max for entry in self._entries),
        )
        return sample

    def _resource_limit_reason(self, sample: ProcessResourceSample) -> str | None:
        rss_limit = self._limits.maximum_rss_bytes
        if rss_limit is not None:
            if sample.rss_bytes is None:
                return "SHADOW_RSS_UNAVAILABLE"
            if sample.rss_bytes > rss_limit:
                return "SHADOW_RSS_LIMIT_EXCEEDED"
        lag_limit = self._limits.maximum_live_dispatch_lag_ms
        if lag_limit is not None and self._maximum_lag_ms > lag_limit:
            return "SHADOW_LAG_LIMIT_EXCEEDED"
        return None

    def _cycle_resource_reason(
        self,
        before: ProcessResourceSample,
        after: ProcessResourceSample,
    ) -> str | None:
        reason = self._resource_limit_reason(after)
        if reason is not None:
            return reason
        cpu_limit = self._limits.maximum_cpu_seconds_per_cycle
        if (
            cpu_limit is not None
            and after.process_cpu_seconds - before.process_cpu_seconds > cpu_limit
        ):
            return "SHADOW_CPU_LIMIT_EXCEEDED"
        return None

    def _exhaust_resources(self, reason_code: str) -> None:
        self._reason_code = reason_code
        self._state = ShadowHostState.RESOURCE_EXHAUSTED
        self._shutdown_all()
        self._events.emit("shadow_host_resource_exhausted", reason_code=reason_code)

    def _shutdown_all(self) -> None:
        for entry in reversed(self._entries):
            try:
                entry.service.shutdown()
            except Exception:
                self._shutdown_failures += 1


def _current_rss_bytes() -> int | None:
    if os.name == "nt":
        return _windows_rss_bytes()
    try:
        resource_module = importlib.import_module("resource")
        usage = resource_module.getrusage(resource_module.RUSAGE_SELF)
        rss = int(usage.ru_maxrss)
    except (ImportError, OSError):
        return None
    return rss if sys.platform == "darwin" else rss * 1_024


def _windows_rss_bytes() -> int | None:
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
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.restype = ctypes.c_void_p
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
            get_current_process(),
            ctypes.byref(counters),
            counters.cb,
        ):
            return None
        return int(counters.working_set_size)
    except OSError:
        return None
