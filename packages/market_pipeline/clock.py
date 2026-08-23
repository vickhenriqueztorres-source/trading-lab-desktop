from __future__ import annotations

import time
from typing import Protocol

from packages.market_pipeline.models import MarketSeriesId, TrustedClosedHorizon


class MonotonicClock(Protocol):
    def now(self) -> float: ...


class SystemMonotonicClock:
    def now(self) -> float:
        return time.monotonic()


def trusted_closed_horizon(
    series_id: MarketSeriesId,
    *,
    source_epoch_seconds: int | None,
    observed_monotonic: float,
) -> TrustedClosedHorizon | None:
    if source_epoch_seconds is None:
        return None
    if source_epoch_seconds <= 0:
        raise ValueError("source epoch must be positive")
    timeframe = series_id.timeframe_seconds
    closed_epoch_seconds = (source_epoch_seconds // timeframe) * timeframe
    return TrustedClosedHorizon(
        source_epoch_seconds=source_epoch_seconds,
        close_epoch_ms=closed_epoch_seconds * 1_000,
        observed_monotonic=observed_monotonic,
    )
