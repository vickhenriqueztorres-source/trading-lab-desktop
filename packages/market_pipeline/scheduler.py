from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from packages.market_pipeline.clock import MonotonicClock
from packages.market_pipeline.health import MarketHealthGate
from packages.market_pipeline.models import (
    MarketHealthReason,
    MarketPipelineMetrics,
    MarketSeriesId,
    MarketSeriesScheduleState,
)
from packages.observability import EventSink, NullEventSink


@dataclass(frozen=True, slots=True)
class ReadOnlyBackfillRetryPolicy:
    maximum_attempts: int = 4
    initial_delay_seconds: float = 1.0
    multiplier: float = 2.0
    maximum_delay_seconds: float = 30.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.maximum_attempts <= 0:
            raise ValueError("read-only retry attempts must be positive")
        if self.initial_delay_seconds <= 0 or self.multiplier < 1:
            raise ValueError("read-only retry backoff is invalid")
        if self.maximum_delay_seconds < self.initial_delay_seconds:
            raise ValueError("read-only retry maximum delay is invalid")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("read-only retry jitter ratio is invalid")

    def delay(self, failure_count: int, *, jitter: Callable[[], float]) -> float:
        if failure_count <= 0:
            raise ValueError("failure count must be positive")
        base = min(
            self.maximum_delay_seconds,
            self.initial_delay_seconds * self.multiplier ** (failure_count - 1),
        )
        sample = jitter()
        if not 0 <= sample <= 1:
            raise ValueError("jitter sample must be between zero and one")
        spread = base * self.jitter_ratio
        return min(self.maximum_delay_seconds, base - spread + 2 * spread * sample)


@dataclass(frozen=True, slots=True)
class BackfillJobResult:
    generation: int
    success: bool
    last_durable_close_epoch: int | None
    recovery_required: bool
    reason: MarketHealthReason


class BackfillJob(Protocol):
    def recover(self, series_id: MarketSeriesId, generation: int) -> BackfillJobResult: ...


class MarketBackfillScheduler:
    """Deterministic monotonic scheduler. It owns no durable or financial state."""

    def __init__(
        self,
        clock: MonotonicClock,
        health: MarketHealthGate,
        job: BackfillJob,
        *,
        interval_seconds: float = 30.0,
        suspension_threshold_seconds: float = 300.0,
        max_backfill_concurrency: int = 2,
        retry_policy: ReadOnlyBackfillRetryPolicy | None = None,
        jitter: Callable[[], float] = lambda: 0.5,
        events: EventSink | None = None,
        metrics: MarketPipelineMetrics | None = None,
    ) -> None:
        if interval_seconds <= 0 or suspension_threshold_seconds <= 0:
            raise ValueError("scheduler durations must be positive")
        if max_backfill_concurrency <= 0:
            raise ValueError("backfill concurrency must be positive")
        self._clock = clock
        self._health = health
        self._job = job
        self._interval = interval_seconds
        self._suspension_threshold = suspension_threshold_seconds
        self._max_concurrency = max_backfill_concurrency
        self._retry = retry_policy or ReadOnlyBackfillRetryPolicy()
        self._jitter = jitter
        self._events = events or NullEventSink()
        self.metrics = metrics or MarketPipelineMetrics()
        self._states: dict[MarketSeriesId, MarketSeriesScheduleState] = {}
        self._order: list[MarketSeriesId] = []
        self._cursor = 0
        self._active: set[MarketSeriesId] = set()
        self._last_tick: float | None = None

    def register(
        self,
        series_id: MarketSeriesId,
        *,
        last_durable_close_epoch: int | None = None,
        initial_delay_seconds: float = 0.0,
    ) -> None:
        if initial_delay_seconds < 0:
            raise ValueError("initial scheduler delay cannot be negative")
        if series_id in self._states:
            return
        now = self._clock.now()
        snapshot = self._health.snapshot(series_id)
        self._states[series_id] = MarketSeriesScheduleState(
            series_id=series_id,
            last_durable_close_epoch=last_durable_close_epoch,
            last_attempt_monotonic=None,
            next_due_monotonic=now + initial_delay_seconds,
            failure_count=0,
            reconnect_generation=snapshot.reconnect_generation,
            health=snapshot.health,
            recovery_required=True,
            reason=snapshot.reason,
        )
        self._order.append(series_id)
        self._events.emit("backfill_scheduled", series_id=series_id.key)

    def state(self, series_id: MarketSeriesId) -> MarketSeriesScheduleState:
        return self._states[series_id]

    def trigger(self, series_id: MarketSeriesId) -> None:
        state = self._states[series_id]
        now = self._clock.now()
        self._states[series_id] = replace(
            state,
            next_due_monotonic=min(state.next_due_monotonic, now),
            recovery_required=True,
        )
        self._events.emit("backfill_scheduled", series_id=series_id.key)

    def tick(self) -> tuple[MarketSeriesId, ...]:
        now = self._clock.now()
        if self._last_tick is not None and now - self._last_tick >= self._suspension_threshold:
            self._handle_suspension(now)
        self._last_tick = now
        due = self._fair_due(now)
        processed: list[MarketSeriesId] = []
        for series_id in due[: self._max_concurrency]:
            if series_id in self._active:
                continue
            self._run_one(series_id, now)
            processed.append(series_id)
        return tuple(processed)

    def _run_one(self, series_id: MarketSeriesId, now: float) -> None:
        state = self._states[series_id]
        generation = self._health.snapshot(series_id).reconnect_generation
        self._active.add(series_id)
        self._events.emit(
            "backfill_started",
            series_id=series_id.key,
            reconnect_generation=generation,
        )
        try:
            result = self._job.recover(series_id, generation)
        except Exception:
            self._record_failure(series_id, state, generation, now)
            return
        finally:
            self._active.discard(series_id)
        if result.generation != generation:
            self._states[series_id] = replace(
                state,
                last_attempt_monotonic=now,
                next_due_monotonic=now,
                reconnect_generation=generation,
                recovery_required=True,
                reason=MarketHealthReason.STALE_CONNECTION_RESPONSE,
            )
            return
        if not result.success:
            self._record_failure(series_id, state, generation, now, reason=result.reason)
            return
        snapshot = self._health.snapshot(series_id)
        self._states[series_id] = replace(
            state,
            last_durable_close_epoch=result.last_durable_close_epoch,
            last_attempt_monotonic=now,
            next_due_monotonic=now + self._interval,
            failure_count=0,
            reconnect_generation=generation,
            health=snapshot.health,
            recovery_required=result.recovery_required,
            reason=snapshot.reason,
        )
        self._events.emit(
            "backfill_completed",
            series_id=series_id.key,
            reconnect_generation=generation,
        )

    def _record_failure(
        self,
        series_id: MarketSeriesId,
        state: MarketSeriesScheduleState,
        generation: int,
        now: float,
        *,
        reason: MarketHealthReason = MarketHealthReason.HISTORY_EXHAUSTED,
    ) -> None:
        failures = state.failure_count + 1
        self.metrics.backfill_failures += 1
        if failures >= self._retry.maximum_attempts:
            self._health.mark_failed(series_id, MarketHealthReason.HISTORY_EXHAUSTED)
            next_due = now + self._retry.maximum_delay_seconds
            next_reason = MarketHealthReason.HISTORY_EXHAUSTED
        else:
            self.metrics.backfill_retries += 1
            next_due = now + self._retry.delay(failures, jitter=self._jitter)
            next_reason = reason
            self._events.emit(
                "backfill_retry_scheduled",
                reason_code=reason.value,
                series_id=series_id.key,
                failure_count=failures,
            )
        snapshot = self._health.snapshot(series_id)
        self._states[series_id] = replace(
            state,
            last_attempt_monotonic=now,
            next_due_monotonic=next_due,
            failure_count=failures,
            reconnect_generation=generation,
            health=snapshot.health,
            recovery_required=True,
            reason=next_reason,
        )
        self._events.emit(
            "backfill_failed",
            reason_code=next_reason.value,
            series_id=series_id.key,
            failure_count=failures,
        )

    def _handle_suspension(self, now: float) -> None:
        for series_id in self._order:
            generation = self._health.mark_suspended(series_id)
            state = self._states[series_id]
            self._states[series_id] = replace(
                state,
                next_due_monotonic=now,
                reconnect_generation=generation,
                recovery_required=True,
                reason=MarketHealthReason.SOURCE_STALE,
            )
        self._events.emit("local_suspend_detected", series_count=len(self._order))

    def _fair_due(self, now: float) -> list[MarketSeriesId]:
        if not self._order:
            return []
        ordered = self._order[self._cursor :] + self._order[: self._cursor]
        due = [
            series_id for series_id in ordered if self._states[series_id].next_due_monotonic <= now
        ]
        if due:
            last = self._order.index(due[-1])
            self._cursor = (last + 1) % len(self._order)
        return due
