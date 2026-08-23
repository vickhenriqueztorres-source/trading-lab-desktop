from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import pytest

from packages.domain.models import Broker
from packages.market_pipeline import (
    BackfillJobResult,
    BackfillPlanner,
    MarketBackfillScheduler,
    MarketHealthGate,
    MarketHealthReason,
    MarketSeriesHealth,
    MarketSeriesId,
    ReadOnlyBackfillRetryPolicy,
    trusted_closed_horizon,
)


@dataclass
class FakeMonotonicClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ScriptedJob:
    def __init__(self, outcomes: tuple[bool, ...] = (True,)) -> None:
        self.outcomes = deque(outcomes)
        self.calls: list[tuple[MarketSeriesId, int]] = []

    def recover(self, series_id: MarketSeriesId, generation: int) -> BackfillJobResult:
        self.calls.append((series_id, generation))
        success = self.outcomes.popleft() if self.outcomes else True
        return BackfillJobResult(
            generation,
            success,
            120_000 if success else None,
            not success,
            MarketHealthReason.INITIAL_WARMUP if success else MarketHealthReason.HISTORY_EXHAUSTED,
        )


def identity(symbol: str = "R_100") -> MarketSeriesId:
    return MarketSeriesId(Broker.DERIV, symbol, symbol, "DIGITAL_OPTION", 60)


def scheduler_for(
    clock: FakeMonotonicClock,
    job: ScriptedJob,
    *,
    initial_delay: float = 10.0,
    concurrency: int = 2,
    retry: ReadOnlyBackfillRetryPolicy | None = None,
) -> tuple[MarketBackfillScheduler, MarketHealthGate, MarketSeriesId]:
    gate = MarketHealthGate()
    series_id = identity()
    gate.register(series_id, required_closed_candles=1)
    scheduler = MarketBackfillScheduler(
        clock,
        gate,
        job,
        interval_seconds=30,
        suspension_threshold_seconds=100,
        max_backfill_concurrency=concurrency,
        retry_policy=retry,
        jitter=lambda: 0.5,
    )
    scheduler.register(series_id, initial_delay_seconds=initial_delay)
    return scheduler, gate, series_id


def test_sch01_job_does_not_run_before_monotonic_due() -> None:
    clock = FakeMonotonicClock()
    job = ScriptedJob()
    scheduler, _, _ = scheduler_for(clock, job)
    assert scheduler.tick() == ()
    clock.advance(9.999)
    assert scheduler.tick() == ()
    assert job.calls == []


def test_sch02_job_runs_at_exact_due_and_sch03_wall_clock_is_irrelevant() -> None:
    clock = FakeMonotonicClock()
    job = ScriptedJob()
    scheduler, _, series_id = scheduler_for(clock, job)
    unrelated_wall_clock = 9_999_999
    unrelated_wall_clock = -unrelated_wall_clock
    assert unrelated_wall_clock < 0
    clock.advance(10)
    assert scheduler.tick() == (series_id,)
    assert len(job.calls) == 1


def test_sch04_large_monotonic_gap_coalesces_to_one_suspension_recovery() -> None:
    clock = FakeMonotonicClock()
    job = ScriptedJob()
    scheduler, gate, series_id = scheduler_for(clock, job, initial_delay=0)
    assert scheduler.tick() == (series_id,)
    clock.advance(7_200)
    assert scheduler.tick() == (series_id,)
    assert len(job.calls) == 2
    assert gate.snapshot(series_id).reconnect_generation == 1
    assert gate.snapshot(series_id).health is MarketSeriesHealth.STALE


def test_sch05_duplicate_triggers_coalesce_and_sch06_series_are_independent() -> None:
    clock = FakeMonotonicClock()
    gate = MarketHealthGate()
    job = ScriptedJob()
    scheduler = MarketBackfillScheduler(clock, gate, job, max_backfill_concurrency=2)
    first = identity("R_100")
    second = identity("R_50")
    for series_id in (first, second):
        gate.register(series_id, required_closed_candles=1)
        scheduler.register(series_id)
    scheduler.trigger(first)
    scheduler.trigger(first)
    assert set(scheduler.tick()) == {first, second}
    assert [item[0] for item in job.calls].count(first) == 1


def test_sch07_global_concurrency_is_bounded_and_fair() -> None:
    clock = FakeMonotonicClock()
    gate = MarketHealthGate()
    job = ScriptedJob()
    scheduler = MarketBackfillScheduler(clock, gate, job, max_backfill_concurrency=1)
    identities = tuple(identity(f"R_{value}") for value in (10, 20, 30))
    for series_id in identities:
        gate.register(series_id, required_closed_candles=1)
        scheduler.register(series_id)
    observed = [scheduler.tick()[0] for _ in identities]
    assert observed == list(identities)


def test_sch08_backoff_grows_sch09_caps_and_sch10_success_resets_failures() -> None:
    clock = FakeMonotonicClock()
    policy = ReadOnlyBackfillRetryPolicy(
        maximum_attempts=5,
        initial_delay_seconds=2,
        multiplier=3,
        maximum_delay_seconds=10,
        jitter_ratio=0,
    )
    job = ScriptedJob((False, False, True))
    scheduler, _, series_id = scheduler_for(
        clock,
        job,
        initial_delay=0,
        retry=policy,
    )
    scheduler.tick()
    assert scheduler.state(series_id).next_due_monotonic == pytest.approx(2)
    clock.advance(2)
    scheduler.tick()
    assert scheduler.state(series_id).next_due_monotonic == pytest.approx(8)
    assert policy.delay(10, jitter=lambda: 0.5) == 10
    clock.advance(6)
    scheduler.tick()
    assert scheduler.state(series_id).failure_count == 0
    assert scheduler.state(series_id).next_due_monotonic == pytest.approx(38)


def test_clk03_missing_source_clock_has_no_trusted_horizon() -> None:
    series_id = identity()
    assert (
        trusted_closed_horizon(
            series_id,
            source_epoch_seconds=None,
            observed_monotonic=1,
        )
        is None
    )
    horizon = trusted_closed_horizon(
        series_id,
        source_epoch_seconds=125,
        observed_monotonic=1,
    )
    assert horizon is not None
    assert horizon.close_epoch_ms == 120_000


def test_backfill_planner_uses_overlap_bounded_chronological_pages() -> None:
    series_id = identity()
    horizon = trusted_closed_horizon(
        series_id,
        source_epoch_seconds=600,
        observed_monotonic=1,
    )
    assert horizon is not None
    planner = BackfillPlanner(max_candles_per_batch=5, backfill_overlap_candles=2)
    plan = planner.plan(
        series_id,
        generation=0,
        horizon=horizon,
        last_durable_close_ms=300_000,
        durable_closed_candles=5,
        required_closed_candles=10,
    )
    assert plan is not None
    assert plan.count == 5
    assert plan.start_close_epoch_ms == 240_000
    assert plan.end_close_epoch_ms == 480_000


def test_retry_exhaustion_fails_closed() -> None:
    clock = FakeMonotonicClock()
    policy = ReadOnlyBackfillRetryPolicy(
        maximum_attempts=2,
        initial_delay_seconds=1,
        maximum_delay_seconds=1,
        jitter_ratio=0,
    )
    job = ScriptedJob((False, False))
    scheduler, gate, series_id = scheduler_for(
        clock,
        job,
        initial_delay=0,
        retry=policy,
    )
    scheduler.tick()
    clock.advance(1)
    scheduler.tick()
    assert gate.snapshot(series_id).health is MarketSeriesHealth.FAILED
