from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from packages.domain.market import MarketTick
from packages.domain.models import Broker
from packages.market_pipeline import (
    ClosedCandleAggregator,
    LiveAggregationStatus,
    MarketSeriesId,
)

BASE = 1_800_000_000


def series() -> MarketSeriesId:
    return MarketSeriesId(
        Broker.DERIV,
        "R_100",
        "R_100",
        "DIGITAL_OPTION",
        60,
    )


def tick(epoch: int, quote: str, *, subscription: str = "sub-1") -> MarketTick:
    return MarketTick(
        broker=Broker.DERIV,
        broker_symbol="R_100",
        epoch=epoch,
        quote=Decimal(quote),
        received_at=datetime.fromtimestamp(epoch, UTC),
        subscription_id=subscription,
        source="FAKE_LIVE",
    )


def test_live_aggregator_closes_exact_ohlc_without_float() -> None:
    aggregator = ClosedCandleAggregator(series(), price_scale=1_000)
    assert aggregator.ingest(tick(BASE, "100.000")).status is LiveAggregationStatus.ACCUMULATED
    aggregator.ingest(tick(BASE + 10, "102.000"))
    aggregator.ingest(tick(BASE + 20, "98.000"))
    aggregator.ingest(tick(BASE + 59, "101.000"))
    closed = aggregator.ingest(tick(BASE + 60, "100.000"))
    assert closed.status is LiveAggregationStatus.CLOSED
    assert closed.candle is not None
    assert closed.candle.price_units == (100_000, 102_000, 98_000, 101_000)
    assert closed.candle.open_time_ms == BASE * 1_000
    assert closed.candle.close_time_ms == (BASE + 60) * 1_000


def test_live_duplicate_out_of_order_and_gap_are_explicit_without_forward_fill() -> None:
    aggregator = ClosedCandleAggregator(series(), price_scale=1_000)
    first = tick(BASE, "100.000")
    aggregator.ingest(first)
    assert aggregator.ingest(first).status is LiveAggregationStatus.DUPLICATE
    gap = aggregator.ingest(tick(BASE + 120, "101.000"))
    assert gap.status is LiveAggregationStatus.GAPPED
    assert gap.candle is not None
    assert gap.candle.open_time_ms == BASE * 1_000
    late = aggregator.ingest(tick(BASE + 61, "99.000"))
    assert late.status is LiveAggregationStatus.OUT_OF_ORDER


def test_live_precision_exceeding_configured_scale_fails_closed() -> None:
    aggregator = ClosedCandleAggregator(series(), price_scale=100)
    with pytest.raises(ValueError, match="precision"):
        aggregator.ingest(tick(BASE, "1.001"))


def test_live_aggregator_soak_10000_ticks_keeps_dedupe_memory_bounded() -> None:
    aggregator = ClosedCandleAggregator(series(), price_scale=1_000, max_seen_ticks=256)
    closed = 0
    for offset in range(10_000):
        quote = "101.000" if offset % 2 else "99.000"
        result = aggregator.ingest(tick(BASE + offset, quote))
        if result.candle is not None:
            closed += 1
    assert closed == 166
    assert aggregator.seen_tick_count == 256
