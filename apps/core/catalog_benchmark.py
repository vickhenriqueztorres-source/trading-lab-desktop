"""Read-only local capacity benchmark for the incremental IQ Option catalog.

The benchmark exercises the same exact-series and incremental indicator-cache identities used by
the runtime.  It deliberately has no worker, socket, broker, order, risk or persistence API.
Measured capacity is an admission input, never an authorization to trade.
"""

from __future__ import annotations

import ctypes
import os
import platform
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

if os.name == "nt":
    from ctypes import wintypes

from apps.core.indicator_cache import IndicatorCache, IndicatorKey, canonical_indicator_params
from apps.core.iqoption_series_hub import IQOPTION_SERIES_PRODUCT, IQOptionSeriesKey
from packages.domain.market import MarketCandle
from packages.domain.models import Broker

MIB = 1024 * 1024
BASELINE_P95_LOCAL_MS = Decimal("100")
BASELINE_P99_LOCAL_MS = Decimal("500")
BASELINE_MAX_RSS_MIB = 512
WARMUP_CANDLES = 20
DEFAULT_EPOCHS = 64


@dataclass(frozen=True, slots=True)
class CatalogBenchmarkScenario:
    name: str
    recipes: int
    series_count: int
    unique_nodes: int
    timeframes: tuple[int, ...]

    def __post_init__(self) -> None:
        if (
            not self.name.strip()
            or self.recipes <= 0
            or self.series_count <= 0
            or self.unique_nodes <= 0
            or self.unique_nodes > self.recipes
            or not self.timeframes
            or any(value <= 0 for value in self.timeframes)
        ):
            raise ValueError("catalog benchmark scenario is invalid")


@dataclass(frozen=True, slots=True)
class CatalogBenchmarkLimits:
    p95_local_ms: Decimal = BASELINE_P95_LOCAL_MS
    p99_local_ms: Decimal = BASELINE_P99_LOCAL_MS
    max_rss_mib: int = BASELINE_MAX_RSS_MIB

    def __post_init__(self) -> None:
        if self.p95_local_ms <= 0 or self.p99_local_ms <= 0 or self.max_rss_mib <= 0:
            raise ValueError("catalog benchmark limits must be positive")
        if self.p95_local_ms > self.p99_local_ms:
            raise ValueError("catalog benchmark p95 limit cannot exceed p99")


@dataclass(frozen=True, slots=True)
class CatalogBenchmarkResult:
    scenario: CatalogBenchmarkScenario
    hardware: dict[str, object]
    recipes: int
    unique_series: int
    active_nodes: int
    node_reuse_ratio: Decimal
    local_samples: int
    local_p50_ms: Decimal
    local_p95_ms: Decimal
    local_p99_ms: Decimal
    local_max_ms: Decimal
    cpu_process_percent: Decimal
    rss_initial_mib: Decimal | None
    rss_peak_mib: Decimal | None
    rss_stable_mib: Decimal | None
    synthetic_series_updates: int
    financial_actions: int
    admitted: bool
    blocking_reasons: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        for name in (
            "node_reuse_ratio",
            "local_p50_ms",
            "local_p95_ms",
            "local_p99_ms",
            "local_max_ms",
            "cpu_process_percent",
            "rss_initial_mib",
            "rss_peak_mib",
            "rss_stable_mib",
        ):
            value = payload[name]
            payload[name] = None if value is None else format(value, "f")
        return payload


DEFAULT_SCENARIOS: tuple[CatalogBenchmarkScenario, ...] = (
    CatalogBenchmarkScenario("10_high_reuse", 10, 1, 1, (60,)),
    CatalogBenchmarkScenario("30_high_reuse", 30, 3, 3, (60,)),
    CatalogBenchmarkScenario("50_low_reuse", 50, 10, 50, (60,)),
    CatalogBenchmarkScenario("100_many_timeframes", 100, 24, 100, (60, 300, 900)),
)


def benchmark_hardware() -> dict[str, object]:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_logical": os.cpu_count() or 0,
        "memory_total_mib": _total_memory_mib(),
        "clock": "time.perf_counter_ns",
        "process_cpu_clock": "time.process_time_ns",
    }


def run_catalog_benchmark(
    scenario: CatalogBenchmarkScenario,
    *,
    epochs: int = DEFAULT_EPOCHS,
    limits: CatalogBenchmarkLimits | None = None,
    monotonic_ns: Callable[[], int] = time.perf_counter_ns,
    process_ns: Callable[[], int] = time.process_time_ns,
    rss_probe: Callable[[], Decimal | None] | None = None,
) -> CatalogBenchmarkResult:
    """Measure a deterministic, entirely local series->indicator workload.

    ``epochs`` is not an availability claim: it is a bounded replay workload.  Each scenario
    receives exact isolated series identities and every recipe references a real cache node.
    """
    if epochs <= WARMUP_CANDLES:
        raise ValueError("catalog benchmark epochs must exceed indicator warmup")
    effective_limits = limits or CatalogBenchmarkLimits()
    resolved_rss_probe = rss_probe or _rss_mib
    cache = IndicatorCache(max_nodes=max(512, scenario.unique_nodes + 1))
    keys = build_catalog_workload(cache, scenario)
    initial_rss = resolved_rss_probe()
    peak_rss = initial_rss
    samples_ns: list[int] = []
    started_wall = monotonic_ns()
    started_cpu = process_ns()

    for epoch in range(epochs):
        started_sample = monotonic_ns()
        for key in keys:
            cache.update(key, catalog_candles_for(key.series_key, epoch + WARMUP_CANDLES))
        samples_ns.append(monotonic_ns() - started_sample)
        observed_rss = resolved_rss_probe()
        if observed_rss is not None and (peak_rss is None or observed_rss > peak_rss):
            peak_rss = observed_rss

    elapsed_wall = max(1, monotonic_ns() - started_wall)
    elapsed_cpu = max(0, process_ns() - started_cpu)
    stable_rss = resolved_rss_probe()
    sample_ms = [Decimal(value) / Decimal(1_000_000) for value in samples_ns]
    local_p50 = _percentile(sample_ms, Decimal("0.50"))
    local_p95 = _percentile(sample_ms, Decimal("0.95"))
    local_p99 = _percentile(sample_ms, Decimal("0.99"))
    local_max = max(sample_ms)
    cpu_percent = Decimal(elapsed_cpu) * Decimal(100) / Decimal(elapsed_wall)
    reasons: list[str] = []
    if local_p95 > effective_limits.p95_local_ms:
        reasons.append("CATALOG_BENCHMARK_P95_LOCAL_EXCEEDED")
    if local_p99 > effective_limits.p99_local_ms:
        reasons.append("CATALOG_BENCHMARK_P99_LOCAL_EXCEEDED")
    if peak_rss is not None and peak_rss > Decimal(effective_limits.max_rss_mib):
        reasons.append("CATALOG_BENCHMARK_RSS_EXCEEDED")
    active_nodes = cache.stats.active_nodes
    return CatalogBenchmarkResult(
        scenario=scenario,
        hardware=benchmark_hardware(),
        recipes=scenario.recipes,
        unique_series=scenario.series_count,
        active_nodes=active_nodes,
        node_reuse_ratio=Decimal(scenario.recipes) / Decimal(active_nodes),
        local_samples=len(sample_ms),
        local_p50_ms=local_p50,
        local_p95_ms=local_p95,
        local_p99_ms=local_p99,
        local_max_ms=local_max,
        cpu_process_percent=cpu_percent,
        rss_initial_mib=initial_rss,
        rss_peak_mib=peak_rss,
        rss_stable_mib=stable_rss,
        synthetic_series_updates=scenario.series_count * epochs,
        financial_actions=0,
        admitted=not reasons,
        blocking_reasons=tuple(reasons),
    )


def build_catalog_workload(
    cache: IndicatorCache,
    scenario: CatalogBenchmarkScenario,
    *,
    generation: str = "benchmark:g1",
    recipe_prefix: str = "benchmark-recipe",
) -> tuple[IndicatorKey, ...]:
    """Acquire one deterministic recipe graph for benchmark and fault harnesses."""

    if not generation.strip() or not recipe_prefix.strip():
        raise ValueError("catalog workload identity is required")
    all_nodes: list[IndicatorKey] = []
    for node_index in range(scenario.unique_nodes):
        timeframe = scenario.timeframes[node_index % len(scenario.timeframes)]
        series_key = IQOptionSeriesKey(
            broker=Broker.IQ_OPTION,
            account_id="benchmark-practice",
            product=IQOPTION_SERIES_PRODUCT,
            generation=generation,
            asset=f"BENCH{node_index % scenario.series_count:02d}-OTC",
            timeframe_seconds=timeframe,
        )
        all_nodes.append(
            IndicatorKey(
                series_key=series_key,
                name="rsi_extreme",
                params=canonical_indicator_params(
                    {"period": 14, "lower": Decimal("30"), "upper": Decimal("70")}
                ),
                auxiliary_identity=(("benchmark_node", str(node_index)),),
            )
        )
    for recipe_index in range(scenario.recipes):
        key = all_nodes[recipe_index % len(all_nodes)]
        cache.acquire(key, recipe_id=f"{recipe_prefix}-{recipe_index}")
    return tuple(all_nodes)


def catalog_candles_for(key: IQOptionSeriesKey, count: int) -> tuple[MarketCandle, ...]:
    """Build deterministic closed candles without broker, socket or persistence access."""

    if count <= 0:
        raise ValueError("catalog candle count must be positive")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    price = Decimal("1.1000") + Decimal(sum(ord(value) for value in key.asset) % 100) / Decimal(
        10000
    )
    candles: list[MarketCandle] = []
    for index in range(count):
        delta = Decimal("0.0003") if (index + len(key.asset)) % 3 else Decimal("-0.0002")
        price += delta
        open_price = price - delta
        open_time = start + timedelta(seconds=index * key.timeframe_seconds)
        candles.append(
            MarketCandle(
                broker=Broker.IQ_OPTION,
                broker_symbol=key.asset,
                timeframe_seconds=key.timeframe_seconds,
                open_time=open_time,
                close_time=open_time + timedelta(seconds=key.timeframe_seconds),
                open=open_price,
                high=max(open_price, price) + Decimal("0.0001"),
                low=min(open_price, price) - Decimal("0.0001"),
                close=price,
                is_closed=True,
                tick_volume=10,
            )
        )
    return tuple(candles)


def _percentile(values: list[Decimal], quantile: Decimal) -> Decimal:
    if not values or not Decimal(0) <= quantile <= Decimal(1):
        raise ValueError("catalog benchmark percentile arguments are invalid")
    ordered = sorted(values)
    index = int((Decimal(len(ordered) - 1) * quantile).to_integral_value())
    return ordered[index]


def _rss_mib() -> Decimal | None:
    if os.name != "nt":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("page_fault_count", wintypes.DWORD),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    get_memory_info = psapi.GetProcessMemoryInfo
    get_memory_info.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    get_memory_info.restype = wintypes.BOOL
    if not get_memory_info(get_current_process(), ctypes.byref(counters), counters.cb):
        return None
    return Decimal(counters.working_set_size) / Decimal(MIB)


def _total_memory_mib() -> int | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_phys", ctypes.c_ulonglong),
            ("avail_phys", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("avail_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("avail_virtual", ctypes.c_ulonglong),
            ("avail_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.total_phys // MIB)


__all__ = [
    "BASELINE_MAX_RSS_MIB",
    "BASELINE_P95_LOCAL_MS",
    "BASELINE_P99_LOCAL_MS",
    "CatalogBenchmarkLimits",
    "CatalogBenchmarkResult",
    "CatalogBenchmarkScenario",
    "DEFAULT_SCENARIOS",
    "benchmark_hardware",
    "build_catalog_workload",
    "catalog_candles_for",
    "run_catalog_benchmark",
]
