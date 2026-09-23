from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies.iqoption_pattern_reversal import (
    IQOPTION_PATTERN_REVERSAL_STRATEGY_ID,
    IQOptionPatternReversalStrategy,
    iqoption_pattern_reversal_manifest,
)
from packages.strategies.models import RuntimeContext

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)


def make_candle(
    symbol: str = "EURUSD-OTC",
    open_price: str = "1.0500",
    high_price: str = "1.0550",
    low_price: str = "1.0450",
    close_price: str = "1.0480",
    open_time: datetime | None = None,
    close_time: datetime | None = None,
    broker: Broker = Broker.IQ_OPTION,
) -> MarketCandle:
    ot = open_time or NOW
    ct = close_time or (ot + timedelta(seconds=60))
    return MarketCandle(
        broker=broker,
        broker_symbol=symbol,
        timeframe_seconds=60,
        open_time=ot,
        close_time=ct,
        open=Decimal(open_price),
        high=Decimal(high_price),
        low=Decimal(low_price),
        close=Decimal(close_price),
        is_closed=True,
    )


def test_manifest_metadata() -> None:
    manifest = iqoption_pattern_reversal_manifest()
    assert manifest.strategy_id == IQOPTION_PATTERN_REVERSAL_STRATEGY_ID
    assert manifest.version == "1.1.0"
    assert manifest.warmup_candles == 2
    assert manifest.supported_timeframes == (60,)


def test_pattern_reversal_call_bullish_engulfing() -> None:
    strategy = IQOptionPatternReversalStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD-OTC",
        account_id="practice",
        strategy_id=IQOPTION_PATTERN_REVERSAL_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # Prev: bearish body |1.0480 - 1.0520| = 0.0040
    prev = make_candle(
        open_price="1.0520", high_price="1.0530", low_price="1.0470", close_price="1.0480"
    )
    # Curr: bullish body 0.0060 (ratio 1.5 >= 1.2), range = 0.0070 (occupancy 85.7% >= 30%)
    curr = make_candle(
        open_price="1.0475",
        high_price="1.0540",
        low_price="1.0470",
        close_price="1.0535",
        open_time=NOW + timedelta(seconds=60),
    )

    decision = strategy.evaluate_decision([prev, curr], context)
    assert decision.direction == Direction.CALL
    assert decision.duration_candles == 1
    assert decision.body_ratio >= Decimal("1.2")
    assert decision.range_occupancy_pct >= Decimal("0.3")
    assert decision.reason_code == "CALL_BULLISH_ENGULFING"


def test_pattern_reversal_put_bearish_engulfing() -> None:
    strategy = IQOptionPatternReversalStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD-OTC",
        account_id="practice",
        strategy_id=IQOPTION_PATTERN_REVERSAL_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # Prev: bullish body |1.0520 - 1.0480| = 0.0040
    prev = make_candle(
        open_price="1.0480", high_price="1.0530", low_price="1.0470", close_price="1.0520"
    )
    # Curr: bearish body 0.0060 (ratio 1.5 >= 1.2), range = 0.0075 (occupancy 80% >= 30%)
    curr = make_candle(
        open_price="1.0525",
        high_price="1.0535",
        low_price="1.0460",
        close_price="1.0465",
        open_time=NOW + timedelta(seconds=60),
    )

    decision = strategy.evaluate_decision([prev, curr], context)
    assert decision.direction == Direction.PUT
    assert decision.duration_candles == 1
    assert decision.body_ratio >= Decimal("1.2")
    assert decision.range_occupancy_pct >= Decimal("0.3")
    assert decision.reason_code == "PUT_BEARISH_ENGULFING"


def test_pattern_reversal_body_ratio_too_small_yields_none() -> None:
    strategy = IQOptionPatternReversalStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD-OTC",
        account_id="practice",
        strategy_id=IQOPTION_PATTERN_REVERSAL_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # Prev: bearish body 0.0050
    prev = make_candle(
        open_price="1.0550", high_price="1.0560", low_price="1.0490", close_price="1.0500"
    )
    # Curr: bullish body 0.0055 (ratio 1.1 < 1.2)
    curr = make_candle(
        open_price="1.0495",
        high_price="1.0560",
        low_price="1.0490",
        close_price="1.0550",
        open_time=NOW + timedelta(seconds=60),
    )

    decision = strategy.evaluate_decision([prev, curr], context)
    assert decision.direction is None
    assert decision.reason_code == "BODY_RATIO_TOO_SMALL"


def test_pattern_reversal_range_occupancy_low_yields_none() -> None:
    strategy = IQOptionPatternReversalStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD-OTC",
        account_id="practice",
        strategy_id=IQOPTION_PATTERN_REVERSAL_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # Prev: bearish body 0.0040
    prev = make_candle(
        open_price="1.0520", high_price="1.0530", low_price="1.0470", close_price="1.0480"
    )
    # Curr: bullish body 0.0050 (ratio 1.25 >= 1.2), but massive wicks: range = 0.0300.
    # Occupancy = 0.0050 / 0.0300 = 16.6% < 30%.
    curr = make_candle(
        open_price="1.0475",
        high_price="1.0700",
        low_price="1.0400",
        close_price="1.0525",
        open_time=NOW + timedelta(seconds=60),
    )

    decision = strategy.evaluate_decision([prev, curr], context)
    assert decision.direction is None
    assert decision.reason_code == "RANGE_OCCUPANCY_LOW"


def test_pattern_reversal_warmup_error() -> None:
    strategy = IQOptionPatternReversalStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD-OTC",
        account_id="practice",
        strategy_id=IQOPTION_PATTERN_REVERSAL_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )
    single_candle = [make_candle()]
    with pytest.raises(ValueError, match="warming up"):
        strategy.evaluate_decision(single_candle, context)
