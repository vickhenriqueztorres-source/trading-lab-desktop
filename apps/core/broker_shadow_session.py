from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from apps.core.shadow_runtime import (
    ReadOnlyMarketClient,
    ReadOnlyMarketSupervisor,
    ShadowRuntimePort,
    ShadowServiceState,
)
from apps.core.worker_supervisor import WorkerHealthState
from packages.domain.models import Broker
from packages.market_pipeline import (
    LiveAggregationResult,
    LiveTickSource,
    MarketSeriesId,
    MonotonicClock,
    SharedMarketTickRouter,
    SharedMarketTickRouterSnapshot,
    SystemMonotonicClock,
)
from packages.observability import EventSink, NullEventSink


class BrokerShadowRuntimeFactory(Protocol):
    def __call__(
        self,
        client: ReadOnlyMarketClient,
        source: LiveTickSource,
        series_id: MarketSeriesId,
    ) -> ShadowRuntimePort: ...


@dataclass(frozen=True, slots=True)
class BrokerShadowSeriesSnapshot:
    series_id: MarketSeriesId
    subscribed: bool
    poll_count: int
    poll_failures: int
    live_dispatch_lag_ms_max: int


@dataclass(frozen=True, slots=True)
class BrokerShadowSessionSnapshot:
    broker: Broker
    state: ShadowServiceState
    worker_health: WorkerHealthState
    registered_series: int
    subscribed_series: int
    start_attempts: int
    recovery_attempts: int
    poll_count: int
    poll_failures: int
    elapsed_monotonic_seconds: float
    router: SharedMarketTickRouterSnapshot | None
    series: tuple[BrokerShadowSeriesSnapshot, ...]


@dataclass(frozen=True, slots=True)
class _SeriesRegistration:
    series_id: MarketSeriesId
    factory: BrokerShadowRuntimeFactory


@dataclass(slots=True)
class _SeriesRuntime:
    series_id: MarketSeriesId
    runtime: ShadowRuntimePort
    poll_count: int = 0
    poll_failures: int = 0


class BrokerShadowSession:
    """One read-only broker worker session shared by multiple shadow runtimes."""

    def __init__(
        self,
        broker: Broker,
        supervisor: ReadOnlyMarketSupervisor,
        *,
        clock: MonotonicClock | None = None,
        max_series: int = 16,
        per_series_queue_size: int = 128,
        events: EventSink | None = None,
    ) -> None:
        if max_series <= 0 or per_series_queue_size <= 0:
            raise ValueError("broker shadow session limits must be positive")
        self._broker = broker
        self._supervisor = supervisor
        self._clock = clock or SystemMonotonicClock()
        self._max_series = max_series
        self._per_series_queue_size = per_series_queue_size
        self._events = events or NullEventSink()
        self._state = ShadowServiceState.STOPPED
        self._registrations: dict[MarketSeriesId, _SeriesRegistration] = {}
        self._runtimes: list[_SeriesRuntime] = []
        self._router: SharedMarketTickRouter | None = None
        self._cursor = 0
        self._started_at: float | None = None
        self._start_attempts = 0
        self._recovery_attempts = 0
        self._poll_count = 0
        self._poll_failures = 0

    @property
    def state(self) -> ShadowServiceState:
        return self._state

    def register(
        self,
        series_id: MarketSeriesId,
        factory: BrokerShadowRuntimeFactory,
    ) -> None:
        if self._state is not ShadowServiceState.STOPPED:
            raise RuntimeError("BROKER_SHADOW_SESSION_REGISTRATION_CLOSED")
        if series_id.broker is not self._broker:
            raise ValueError("broker shadow session series broker does not match")
        if series_id in self._registrations:
            raise ValueError("broker shadow session series is already registered")
        if len(self._registrations) >= self._max_series:
            raise ValueError("broker shadow session series limit exceeded")
        self._registrations[series_id] = _SeriesRegistration(series_id, factory)

    def start(self) -> bool:
        if self._state is ShadowServiceState.RUNNING:
            return True
        if self._state is not ShadowServiceState.STOPPED:
            raise RuntimeError("BROKER_SHADOW_SESSION_ALREADY_STARTED")
        if not self._registrations:
            raise RuntimeError("BROKER_SHADOW_SESSION_HAS_NO_SERIES")
        self._state = ShadowServiceState.STARTING
        self._start_attempts += 1
        self._started_at = self._clock.now()
        self._events.emit("broker_shadow_session_starting", broker=self._broker.value)
        try:
            client = self._supervisor.start()
            self._install_runtimes(client)
            ready = self._start_runtimes(recovery=False)
        except Exception:
            self._state = ShadowServiceState.FAILED
            self._events.emit(
                "broker_shadow_session_failed",
                broker=self._broker.value,
                reason_code="BROKER_SHADOW_START_FAILED",
            )
            self._shutdown_resources()
            raise
        self._state = ShadowServiceState.RUNNING if ready else ShadowServiceState.RECOVERING
        self._events.emit(
            "broker_shadow_session_started",
            broker=self._broker.value,
            ready=ready,
            series_count=len(self._runtimes),
        )
        return ready

    def poll_once(self, *, timeout: float) -> LiveAggregationResult | None:
        if timeout <= 0:
            raise ValueError("broker shadow session poll timeout must be positive")
        if self._state is ShadowServiceState.STOPPED or not self._runtimes:
            raise RuntimeError("BROKER_SHADOW_SESSION_NOT_STARTED")
        if self._state is not ShadowServiceState.RUNNING:
            return None
        if self._supervisor.health_state is not WorkerHealthState.READY:
            self._poll_failures += 1
            self._disconnect_all()
            self._state = ShadowServiceState.RECOVERING
            self._events.emit(
                "broker_shadow_session_recovery_required",
                broker=self._broker.value,
                reason_code="BROKER_SHADOW_WORKER_NOT_READY",
            )
            return None
        entry = self._next_runtime()
        self._poll_count += 1
        entry.poll_count += 1
        try:
            return entry.runtime.poll_once(timeout=timeout)
        except Exception:
            self._poll_failures += 1
            entry.poll_failures += 1
            self._disconnect_all()
            self._state = ShadowServiceState.RECOVERING
            self._events.emit(
                "broker_shadow_session_recovery_required",
                broker=self._broker.value,
                reason_code="BROKER_SHADOW_POLL_FAILED",
                series_id=entry.series_id.key,
            )
            raise

    def recover(self) -> bool:
        if self._state is ShadowServiceState.STOPPED:
            raise RuntimeError("BROKER_SHADOW_SESSION_NOT_STARTED")
        self._state = ShadowServiceState.RECOVERING
        self._recovery_attempts += 1
        self._disconnect_all()
        self._stop_runtimes()
        self._events.emit("broker_shadow_session_recovery_started", broker=self._broker.value)
        try:
            client = self._supervisor.restart()
            self._install_runtimes(client)
            ready = self._start_runtimes(recovery=True)
        except Exception:
            self._state = ShadowServiceState.FAILED
            self._events.emit(
                "broker_shadow_session_failed",
                broker=self._broker.value,
                reason_code="BROKER_SHADOW_RECOVERY_FAILED",
            )
            self._shutdown_resources()
            raise
        self._state = ShadowServiceState.RUNNING if ready else ShadowServiceState.RECOVERING
        self._events.emit(
            "broker_shadow_session_recovery_completed",
            broker=self._broker.value,
            ready=ready,
        )
        return ready

    def shutdown(self) -> None:
        if self._state is ShadowServiceState.STOPPED:
            return
        self._shutdown_resources()
        self._state = ShadowServiceState.STOPPED
        self._started_at = None
        self._events.emit("broker_shadow_session_stopped", broker=self._broker.value)

    def snapshot(self) -> BrokerShadowSessionSnapshot:
        elapsed = (
            0.0 if self._started_at is None else max(0.0, self._clock.now() - self._started_at)
        )
        series = tuple(
            BrokerShadowSeriesSnapshot(
                series_id=entry.series_id,
                subscribed=entry.runtime.subscribed,
                poll_count=entry.poll_count,
                poll_failures=entry.poll_failures,
                live_dispatch_lag_ms_max=entry.runtime.metrics.live_dispatch_lag_ms_max,
            )
            for entry in self._runtimes
        )
        return BrokerShadowSessionSnapshot(
            broker=self._broker,
            state=self._state,
            worker_health=self._supervisor.health_state,
            registered_series=len(self._registrations),
            subscribed_series=sum(1 for entry in self._runtimes if entry.runtime.subscribed),
            start_attempts=self._start_attempts,
            recovery_attempts=self._recovery_attempts,
            poll_count=self._poll_count,
            poll_failures=self._poll_failures,
            elapsed_monotonic_seconds=elapsed,
            router=self._router.snapshot() if self._router is not None else None,
            series=series,
        )

    def _install_runtimes(self, client: ReadOnlyMarketClient) -> None:
        router = SharedMarketTickRouter(
            self._broker,
            client,
            max_series=self._max_series,
            per_series_queue_size=self._per_series_queue_size,
            events=self._events,
        )
        runtimes: list[_SeriesRuntime] = []
        for registration in self._registrations.values():
            source = router.register(registration.series_id)
            runtimes.append(
                _SeriesRuntime(
                    registration.series_id,
                    registration.factory(client, source, registration.series_id),
                )
            )
        self._router = router
        self._runtimes = runtimes
        self._cursor = 0

    def _start_runtimes(self, *, recovery: bool) -> bool:
        ready = True
        for entry in self._runtimes:
            current = entry.runtime.recover_and_restore() if recovery else entry.runtime.start()
            ready = ready and current
        return ready

    def _next_runtime(self) -> _SeriesRuntime:
        entry = self._runtimes[self._cursor % len(self._runtimes)]
        self._cursor = (self._cursor + 1) % len(self._runtimes)
        return entry

    def _disconnect_all(self) -> None:
        for entry in self._runtimes:
            if entry.runtime.subscribed:
                entry.runtime.on_disconnect()

    def _stop_runtimes(self) -> None:
        previous = self._runtimes
        self._runtimes = []
        self._router = None
        for entry in previous:
            entry.runtime.stop()

    def _shutdown_resources(self) -> None:
        try:
            self._stop_runtimes()
        finally:
            self._supervisor.shutdown()
