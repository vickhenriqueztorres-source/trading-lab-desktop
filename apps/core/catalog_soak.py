"""Local CAT-19 replay and failure harness for the incremental catalog.

The harness exercises the production series identities, bounded scheduler and
incremental indicator cache.  It has no broker transport, credentials,
persistence, risk service or financial submission API.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal

from apps.core.catalog_benchmark import (
    DEFAULT_SCENARIOS,
    WARMUP_CANDLES,
    CatalogBenchmarkScenario,
    benchmark_hardware,
    build_catalog_workload,
    catalog_candles_for,
    run_catalog_benchmark,
)
from apps.core.indicator_cache import (
    IndicatorCache,
    IndicatorCacheReason,
    IndicatorNodeState,
    ShadowComparisonStatus,
)
from apps.core.iqoption_connection_safety import IQOptionMessageBudget
from apps.core.iqoption_series_hub import (
    IQOptionSeriesHub,
    IQOptionSeriesPriority,
    IQOptionSeriesRequest,
)
from packages.domain.market import MarketCandle

MINUTES_PER_DAY = 24 * 60
CATALOG_SOAK_SCENARIO = DEFAULT_SCENARIOS[1]


@dataclass(frozen=True, slots=True)
class CatalogFaultEvidence:
    name: str
    passed: bool
    details: dict[str, object]

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CatalogReplayResult:
    scenario: str
    simulated_minutes: int
    local_outputs: int
    unique_output_identities: int
    ready_outputs: int
    signal_outputs: int
    indicator_calculations: int
    shadow_matches: int
    shadow_mismatches: int
    elapsed_seconds: Decimal
    cpu_process_percent: Decimal
    network_calls: int = 0
    financial_actions: int = 0

    @property
    def passed(self) -> bool:
        expected = CATALOG_SOAK_SCENARIO.unique_nodes * self.simulated_minutes
        return (
            self.local_outputs == expected
            and self.unique_output_identities == expected
            and self.ready_outputs == expected
            and self.shadow_matches == CATALOG_SOAK_SCENARIO.unique_nodes
            and self.shadow_mismatches == 0
            and self.network_calls == 0
            and self.financial_actions == 0
        )

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["elapsed_seconds"] = format(self.elapsed_seconds, "f")
        payload["cpu_process_percent"] = format(self.cpu_process_percent, "f")
        payload["passed"] = self.passed
        return payload


@dataclass(frozen=True, slots=True)
class CatalogProcessSoakResult:
    requested_wall_seconds: Decimal
    elapsed_seconds: Decimal
    batches: int
    rejected_batches: int
    peak_rss_mib: Decimal | None
    network_calls: int = 0
    financial_actions: int = 0

    @property
    def passed(self) -> bool:
        return (
            self.batches > 0
            and self.rejected_batches == 0
            and self.network_calls == 0
            and self.financial_actions == 0
        )

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        for name in ("requested_wall_seconds", "elapsed_seconds", "peak_rss_mib"):
            value = payload[name]
            payload[name] = None if value is None else format(value, "f")
        payload["passed"] = self.passed
        return payload


class _LocalHistoryClient:
    """In-memory history source used only to drive the production series hub."""

    def __init__(self, candles: Sequence[MarketCandle]) -> None:
        self._candles = tuple(candles)
        self.requests = 0

    def market_history(
        self,
        symbol: str,
        *,
        style: str,
        count: int,
        timeframe_seconds: int,
    ) -> tuple[Sequence[object], Sequence[MarketCandle]]:
        del symbol, style, timeframe_seconds
        self.requests += 1
        return (), self._candles[-count:]


def run_catalog_replay(
    *,
    simulated_minutes: int = MINUTES_PER_DAY,
    monotonic_ns: Callable[[], int] = time.perf_counter_ns,
    process_ns: Callable[[], int] = time.process_time_ns,
) -> CatalogReplayResult:
    """Replay closed M1 candles through the admitted 30-recipe graph."""

    if simulated_minutes <= 0:
        raise ValueError("catalog replay duration must be positive")
    cache = IndicatorCache(max_nodes=64)
    keys = build_catalog_workload(
        cache,
        CATALOG_SOAK_SCENARIO,
        generation="cat19-replay:g1",
        recipe_prefix="cat19-replay",
    )
    histories = {
        key: catalog_candles_for(key.series_key, WARMUP_CANDLES + simulated_minutes) for key in keys
    }
    output_identities: set[tuple[object, ...]] = set()
    local_outputs = 0
    ready_outputs = 0
    signal_outputs = 0
    started_wall = monotonic_ns()
    started_cpu = process_ns()
    for minute in range(simulated_minutes):
        end = WARMUP_CANDLES + minute + 1
        for key in keys:
            output = cache.update(key, histories[key][:end])
            local_outputs += 1
            output_identities.add(
                (
                    key.series_key,
                    output.close_epoch,
                    output.direction,
                    output.value,
                    output.meta,
                )
            )
            ready_outputs += output.state is IndicatorNodeState.READY
            signal_outputs += output.direction != "none"
    elapsed_wall_ns = max(1, monotonic_ns() - started_wall)
    elapsed_cpu_ns = max(0, process_ns() - started_cpu)
    shadow_matches = 0
    for key in keys:
        comparison = cache.shadow_compare(key, histories[key])
        shadow_matches += comparison.status is ShadowComparisonStatus.MATCH
    return CatalogReplayResult(
        scenario=CATALOG_SOAK_SCENARIO.name,
        simulated_minutes=simulated_minutes,
        local_outputs=local_outputs,
        unique_output_identities=len(output_identities),
        ready_outputs=ready_outputs,
        signal_outputs=signal_outputs,
        indicator_calculations=cache.stats.indicator_calculations,
        shadow_matches=shadow_matches,
        shadow_mismatches=cache.stats.shadow_mismatches,
        elapsed_seconds=Decimal(elapsed_wall_ns) / Decimal(1_000_000_000),
        cpu_process_percent=(Decimal(elapsed_cpu_ns) * Decimal(100) / Decimal(elapsed_wall_ns)),
    )


def run_catalog_fault_matrix() -> tuple[CatalogFaultEvidence, ...]:
    """Exercise every CAT-19 local catalog fault with explicit accounting."""

    return (
        _reconnect_evidence(),
        _suspension_gap_evidence(),
        _catalog_swap_evidence(),
        _slow_market_evidence(),
        _queue_full_evidence(),
        _shutdown_during_bootstrap_evidence(),
    )


def run_catalog_process_soak(
    *,
    wall_seconds: Decimal,
    batch_epochs: int = 64,
    scenario: CatalogBenchmarkScenario = CATALOG_SOAK_SCENARIO,
    monotonic: Callable[[], float] = time.perf_counter,
) -> CatalogProcessSoakResult:
    """Keep a real local process under admitted catalog load for a wall-clock duration."""

    if wall_seconds < 0:
        raise ValueError("catalog process soak duration cannot be negative")
    started = monotonic()
    deadline = started + float(wall_seconds)
    batches = 0
    rejected = 0
    peak_rss: Decimal | None = None
    while batches == 0 or monotonic() < deadline:
        result = run_catalog_benchmark(scenario, epochs=batch_epochs)
        batches += 1
        rejected += not result.admitted
        if result.rss_peak_mib is not None and (peak_rss is None or result.rss_peak_mib > peak_rss):
            peak_rss = result.rss_peak_mib
    elapsed = Decimal(str(monotonic() - started))
    return CatalogProcessSoakResult(
        requested_wall_seconds=wall_seconds,
        elapsed_seconds=elapsed,
        batches=batches,
        rejected_batches=rejected,
        peak_rss_mib=peak_rss,
    )


def _reconnect_evidence() -> CatalogFaultEvidence:
    cache = IndicatorCache(max_nodes=16)
    old = build_catalog_workload(
        cache,
        CATALOG_SOAK_SCENARIO,
        generation="cat19:old",
        recipe_prefix="old",
    )
    old_outputs = [cache.update(key, catalog_candles_for(key.series_key, 21)) for key in old]
    for key in old:
        cache.invalidate_series(key.series_key, reason=IndicatorCacheReason.SERIES_MISMATCH)
    new = build_catalog_workload(
        cache,
        CATALOG_SOAK_SCENARIO,
        generation="cat19:new",
        recipe_prefix="new",
    )
    new_outputs = [cache.update(key, catalog_candles_for(key.series_key, 21)) for key in new]
    stale = [cache.update(key, catalog_candles_for(key.series_key, 22)) for key in old]
    for index in range(CATALOG_SOAK_SCENARIO.recipes):
        cache.release_recipe(f"old-{index}")
    passed = (
        all(item.state is IndicatorNodeState.READY for item in old_outputs)
        and all(item.state is IndicatorNodeState.READY for item in new_outputs)
        and all(item.state is IndicatorNodeState.INVALID for item in stale)
        and cache.stats.active_nodes == CATALOG_SOAK_SCENARIO.unique_nodes
    )
    return CatalogFaultEvidence(
        "reconnect_generation_fencing",
        passed,
        {
            "stale_outputs_blocked": sum(
                item.state is IndicatorNodeState.INVALID for item in stale
            ),
            "new_outputs_ready": sum(
                item.state is IndicatorNodeState.READY for item in new_outputs
            ),
            "active_nodes_after_retirement": cache.stats.active_nodes,
        },
    )


def _suspension_gap_evidence() -> CatalogFaultEvidence:
    scenario = CatalogBenchmarkScenario("gap", 1, 1, 1, (60,))
    cache = IndicatorCache(max_nodes=4)
    old = build_catalog_workload(cache, scenario, generation="cat19:gap", recipe_prefix="gap")[0]
    candles = list(catalog_candles_for(old.series_key, 21))
    candles.pop(10)
    blocked = cache.update(old, candles)
    recovered = build_catalog_workload(
        cache,
        scenario,
        generation="cat19:gap-recovered",
        recipe_prefix="recovered",
    )[0]
    ready = cache.update(recovered, catalog_candles_for(recovered.series_key, 21))
    passed = (
        blocked.reason is IndicatorCacheReason.SERIES_GAP
        and blocked.state is IndicatorNodeState.INVALID
        and ready.state is IndicatorNodeState.READY
    )
    return CatalogFaultEvidence(
        "suspension_gap_fail_closed_then_recover",
        passed,
        {"blocked_reason": blocked.reason.value, "recovered_state": ready.state.value},
    )


def _catalog_swap_evidence() -> CatalogFaultEvidence:
    cache = IndicatorCache(max_nodes=16)
    old = build_catalog_workload(
        cache,
        CATALOG_SOAK_SCENARIO,
        generation="catalog:v1",
        recipe_prefix="v1",
    )
    for key in old:
        cache.update(key, catalog_candles_for(key.series_key, 21))
    replacement = DEFAULT_SCENARIOS[0]
    new = build_catalog_workload(
        cache,
        replacement,
        generation="catalog:v2",
        recipe_prefix="v2",
    )
    new_ready = [cache.update(key, catalog_candles_for(key.series_key, 21)) for key in new]
    for index in range(CATALOG_SOAK_SCENARIO.recipes):
        cache.release_recipe(f"v1-{index}")
    passed = (
        all(item.state is IndicatorNodeState.READY for item in new_ready)
        and cache.stats.active_nodes == replacement.unique_nodes
        and all(cache.node_ref_count(key) == 0 for key in old)
    )
    return CatalogFaultEvidence(
        "atomic_catalog_swap_retirement",
        passed,
        {
            "replacement_nodes": cache.stats.active_nodes,
            "retired_nodes_remaining": sum(cache.node_ref_count(key) > 0 for key in old),
        },
    )


def _slow_market_evidence() -> CatalogFaultEvidence:
    cache = IndicatorCache(max_nodes=16)
    keys = build_catalog_workload(
        cache,
        CATALOG_SOAK_SCENARIO,
        generation="cat19:slow",
        recipe_prefix="slow",
    )
    first = {key: catalog_candles_for(key.series_key, 21) for key in keys}
    initial = [cache.update(key, first[key]) for key in keys]
    calculations_before = cache.stats.indicator_calculations
    repeats = [cache.update(key, first[key]) for _ in range(10) for key in keys]
    calculations_after_repeat = cache.stats.indicator_calculations
    resumed = [cache.update(key, catalog_candles_for(key.series_key, 22)) for key in keys]
    calculations_after_resume = cache.stats.indicator_calculations
    passed = (
        all(item.state is IndicatorNodeState.READY for item in initial)
        and len({(item.key, item.close_epoch) for item in repeats}) == len(keys)
        and calculations_before == calculations_after_repeat
        and calculations_after_resume - calculations_after_repeat == len(keys)
        and all(item.state is IndicatorNodeState.READY for item in resumed)
    )
    return CatalogFaultEvidence(
        "slow_market_no_duplicate_then_resume",
        passed,
        {
            "repeated_notifications": len(repeats),
            "extra_calculations_while_stale": calculations_after_repeat - calculations_before,
            "calculations_on_new_close": calculations_after_resume - calculations_after_repeat,
        },
    )


def _queue_full_evidence() -> CatalogFaultEvidence:
    hub = IQOptionSeriesHub(
        message_budget=IQOptionMessageBudget(limit=60),
        monotonic=lambda: 1.0,
        utc_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        max_queue_size=2,
    )
    cache = IndicatorCache(max_nodes=16)
    keys = build_catalog_workload(
        cache,
        CatalogBenchmarkScenario("queue", 5, 5, 5, (60,)),
        generation="cat19:queue",
        recipe_prefix="queue",
    )
    requested = tuple(
        IQOptionSeriesRequest(key.series_key, 15, 1, IQOptionSeriesPriority.STEADY) for key in keys
    )
    accepted = hub.schedule(requested)
    rejected = len(requested) - len(accepted)
    passed = len(accepted) == 2 and rejected == 3 and hub.stats.queue_full == rejected
    return CatalogFaultEvidence(
        "bounded_queue_explicit_backpressure",
        passed,
        {
            "requested": len(requested),
            "accepted": len(accepted),
            "explicitly_rejected": rejected,
            "queue_full_counter": hub.stats.queue_full,
        },
    )


def _shutdown_during_bootstrap_evidence() -> CatalogFaultEvidence:
    cache = IndicatorCache(max_nodes=4)
    scenario = CatalogBenchmarkScenario("bootstrap", 1, 1, 1, (60,))
    key = build_catalog_workload(
        cache,
        scenario,
        generation="cat19:bootstrap",
        recipe_prefix="bootstrap",
    )[0]
    candles = catalog_candles_for(key.series_key, 5)
    hub = IQOptionSeriesHub(
        message_budget=IQOptionMessageBudget(limit=60),
        monotonic=lambda: 1.0,
        utc_clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    client = _LocalHistoryClient(candles)
    snapshot = hub.snapshot(client=client, key=key.series_key, warmup_required=15, close_epoch=1)
    warming = cache.update(key, candles)
    cache.invalidate_all(reason=IndicatorCacheReason.SERIES_MISMATCH)
    hub.invalidate(reason="shutdown")
    passed = (
        snapshot.snapshot is not None
        and not snapshot.snapshot.complete_warmup
        and warming.state is IndicatorNodeState.WARMING_UP
        and cache.stats.active_nodes == 0
        and hub.active_series_count == 0
        and client.requests == 1
    )
    return CatalogFaultEvidence(
        "shutdown_during_bootstrap_cleanup",
        passed,
        {
            "bootstrap_state": warming.state.value,
            "active_nodes_after_shutdown": cache.stats.active_nodes,
            "active_series_after_shutdown": hub.active_series_count,
            "local_history_reads": client.requests,
        },
    )


def catalog_soak_payload(
    replay: CatalogReplayResult,
    faults: Sequence[CatalogFaultEvidence],
    process_soak: CatalogProcessSoakResult,
) -> dict[str, object]:
    return {
        "event": "catalog_soak_completed",
        "hardware": benchmark_hardware(),
        "replay": replay.to_payload(),
        "faults": [fault.to_payload() for fault in faults],
        "process_soak": process_soak.to_payload(),
        "network_calls": 0,
        "financial_actions": 0,
        "passed": replay.passed and all(fault.passed for fault in faults) and process_soak.passed,
    }


__all__ = [
    "CATALOG_SOAK_SCENARIO",
    "MINUTES_PER_DAY",
    "CatalogFaultEvidence",
    "CatalogProcessSoakResult",
    "CatalogReplayResult",
    "catalog_soak_payload",
    "run_catalog_fault_matrix",
    "run_catalog_process_soak",
    "run_catalog_replay",
]
