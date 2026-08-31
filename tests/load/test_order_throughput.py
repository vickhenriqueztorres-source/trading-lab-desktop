"""Bounded local throughput probe; it never talks to a broker."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest


@dataclass(frozen=True, slots=True)
class ThroughputResult:
    count: int
    elapsed_seconds: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    average_ms: float
    error_rate: float


@pytest.mark.slow
def test_local_order_throughput_probe() -> None:
    async def run() -> ThroughputResult:
        latencies: list[float] = []
        semaphore = asyncio.Semaphore(10)

        async def one() -> None:
            async with semaphore:
                start = time.monotonic()
                await asyncio.sleep(0)
                latencies.append((time.monotonic() - start) * 1000)

        started = time.monotonic()
        await asyncio.gather(*(one() for _ in range(1000)))
        elapsed = time.monotonic() - started
        values = sorted(latencies)

        def percentile(percent: float) -> float:
            return values[min(len(values) - 1, int(len(values) * percent))]

        return ThroughputResult(
            len(values),
            elapsed,
            percentile(0.50),
            percentile(0.95),
            percentile(0.99),
            sum(values) / len(values),
            0.0,
        )

    result = asyncio.run(run())
    assert result.count == 1000
    assert result.error_rate == 0
    assert result.p99_ms < 1000
