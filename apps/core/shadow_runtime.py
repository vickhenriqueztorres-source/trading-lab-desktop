from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from apps.core.worker_supervisor import WorkerHealthState
from packages.brokers.deriv.contracts import DerivCandleHistorySource
from packages.domain.market import BrokerClockSnapshot
from packages.market_pipeline import (
    LiveAggregationResult,
    LiveTickSource,
    MarketPipelineMetrics,
    MonotonicClock,
    SystemMonotonicClock,
)
from packages.observability import EventSink, NullEventSink


class ShadowServiceState(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    RECOVERING = "RECOVERING"
    FAILED = "FAILED"


class ReadOnlyMarketClient(LiveTickSource, DerivCandleHistorySource, Protocol):
    def broker_clock(self) -> BrokerClockSnapshot: ...


class ReadOnlyMarketSupervisor(Protocol):
    @property
    def health_state(self) -> WorkerHealthState: ...

    def start(self) -> ReadOnlyMarketClient: ...

    def restart(self) -> ReadOnlyMarketClient: ...

    def shutdown(self, grace_seconds: float = 1.0) -> None: ...


class ShadowRuntimePort(Protocol):
    metrics: MarketPipelineMetrics

    @property
    def subscribed(self) -> bool: ...

    def start(self) -> bool: ...

    def recover_and_restore(self) -> bool: ...

    def poll_once(self, *, timeout: float) -> LiveAggregationResult | None: ...

    def on_disconnect(self) -> int: ...

    def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ShadowServiceSnapshot:
    state: ShadowServiceState
    worker_health: WorkerHealthState
    subscribed: bool
    start_attempts: int
    recovery_attempts: int
    poll_count: int
    poll_failures: int
    elapsed_monotonic_seconds: float
    live_dispatch_lag_ms_max: int


class SupervisedShadowRuntime:
    """Caller-driven Core lifecycle for a read-only worker and shadow runtime."""

    def __init__(
        self,
        supervisor: ReadOnlyMarketSupervisor,
        runtime_factory: Callable[[ReadOnlyMarketClient], ShadowRuntimePort],
        *,
        clock: MonotonicClock | None = None,
        events: EventSink | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._runtime_factory = runtime_factory
        self._clock = clock or SystemMonotonicClock()
        self._events = events or NullEventSink()
        self._state = ShadowServiceState.STOPPED
        self._runtime: ShadowRuntimePort | None = None
        self._started_at: float | None = None
        self._start_attempts = 0
        self._recovery_attempts = 0
        self._poll_count = 0
        self._poll_failures = 0

    @property
    def state(self) -> ShadowServiceState:
        return self._state

    def snapshot(self) -> ShadowServiceSnapshot:
        runtime = self._runtime
        elapsed = (
            0.0 if self._started_at is None else max(0.0, self._clock.now() - self._started_at)
        )
        return ShadowServiceSnapshot(
            state=self._state,
            worker_health=self._supervisor.health_state,
            subscribed=runtime.subscribed if runtime is not None else False,
            start_attempts=self._start_attempts,
            recovery_attempts=self._recovery_attempts,
            poll_count=self._poll_count,
            poll_failures=self._poll_failures,
            elapsed_monotonic_seconds=elapsed,
            live_dispatch_lag_ms_max=(
                runtime.metrics.live_dispatch_lag_ms_max if runtime is not None else 0
            ),
        )

    def start(self) -> bool:
        if self._state is ShadowServiceState.RUNNING:
            return True
        if self._state is not ShadowServiceState.STOPPED:
            raise RuntimeError("SHADOW_SERVICE_ALREADY_STARTED")
        self._state = ShadowServiceState.STARTING
        self._start_attempts += 1
        self._started_at = self._clock.now()
        self._events.emit("shadow_service_starting")
        try:
            client = self._supervisor.start()
            runtime = self._runtime_factory(client)
            self._runtime = runtime
            ready = runtime.start()
        except Exception:
            self._state = ShadowServiceState.FAILED
            self._events.emit("shadow_service_failed", reason_code="SHADOW_START_FAILED")
            self._shutdown_resources()
            raise
        self._state = ShadowServiceState.RUNNING if ready else ShadowServiceState.RECOVERING
        self._events.emit("shadow_service_started", ready=ready)
        return ready

    def poll_once(self, *, timeout: float) -> LiveAggregationResult | None:
        if timeout <= 0:
            raise ValueError("shadow service poll timeout must be positive")
        runtime = self._runtime
        if runtime is None or self._state is ShadowServiceState.STOPPED:
            raise RuntimeError("SHADOW_SERVICE_NOT_STARTED")
        if self._state is not ShadowServiceState.RUNNING:
            return None
        if self._supervisor.health_state is not WorkerHealthState.READY:
            if runtime.subscribed:
                runtime.on_disconnect()
            self._poll_failures += 1
            self._state = ShadowServiceState.RECOVERING
            self._events.emit(
                "shadow_service_recovery_required",
                reason_code="SHADOW_WORKER_NOT_READY",
            )
            return None
        self._poll_count += 1
        try:
            return runtime.poll_once(timeout=timeout)
        except Exception:
            self._poll_failures += 1
            self._state = ShadowServiceState.RECOVERING
            self._events.emit(
                "shadow_service_recovery_required",
                reason_code="SHADOW_POLL_FAILED",
            )
            raise

    def recover(self) -> bool:
        if self._state is ShadowServiceState.STOPPED:
            raise RuntimeError("SHADOW_SERVICE_NOT_STARTED")
        self._state = ShadowServiceState.RECOVERING
        self._recovery_attempts += 1
        previous = self._runtime
        if previous is not None:
            if previous.subscribed:
                previous.on_disconnect()
            previous.stop()
        self._events.emit("shadow_service_recovery_started")
        try:
            client = self._supervisor.restart()
            runtime = self._runtime_factory(client)
            self._runtime = runtime
            ready = runtime.recover_and_restore()
        except Exception:
            self._state = ShadowServiceState.FAILED
            self._events.emit("shadow_service_failed", reason_code="SHADOW_RECOVERY_FAILED")
            self._shutdown_resources()
            raise
        self._state = ShadowServiceState.RUNNING if ready else ShadowServiceState.RECOVERING
        self._events.emit("shadow_service_recovery_completed", ready=ready)
        return ready

    def shutdown(self) -> None:
        if self._state is ShadowServiceState.STOPPED:
            return
        self._shutdown_resources()
        self._state = ShadowServiceState.STOPPED
        self._started_at = None
        self._events.emit("shadow_service_stopped")

    def _shutdown_resources(self) -> None:
        runtime = self._runtime
        self._runtime = None
        try:
            if runtime is not None:
                runtime.stop()
        finally:
            self._supervisor.shutdown()
