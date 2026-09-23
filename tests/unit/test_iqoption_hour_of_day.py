from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies.iqoption_hour_of_day import (
    IQOPTION_HOUR_OF_DAY_STRATEGY_ID,
    IQOptionHourOfDayStrategy,
    iqoption_hour_of_day_manifest,
)
from packages.strategies.models import RuntimeContext


def make_candle(
    dt: datetime,
    is_bullish: bool,
    symbol: str = "EURUSD",
) -> MarketCandle:
    open_price = Decimal("1.0800")
    close_price = Decimal("1.0810") if is_bullish else Decimal("1.0790")
    return MarketCandle(
        broker=Broker.IQ_OPTION,
        broker_symbol=symbol,
        timeframe_seconds=60,
        open_time=dt,
        close_time=dt + timedelta(seconds=60),
        open=open_price,
        high=Decimal("1.0820"),
        low=Decimal("1.0780"),
        close=close_price,
        is_closed=True,
    )


def test_hour_of_day_manifest() -> None:
    manifest = iqoption_hour_of_day_manifest()
    assert manifest.strategy_id == IQOPTION_HOUR_OF_DAY_STRATEGY_ID
    assert manifest.version == "1.0.0"
    assert manifest.warmup_candles == 60


def test_hour_of_day_insufficient_observations() -> None:
    strategy = IQOptionHourOfDayStrategy(min_observations=60)
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD",
        account_id="practice",
        strategy_id=IQOPTION_HOUR_OF_DAY_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    base = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
    # Only 30 candles at hour 14
    candles = [make_candle(base + timedelta(minutes=i), is_bullish=True) for i in range(30)]

    decision = strategy.evaluate_decision(candles, context)
    assert decision.direction is None
    assert decision.reason_code == "INSUFFICIENT_OBSERVATIONS"
    assert decision.directional_observations == 30


def test_hour_of_day_call_when_bullish_ratio_ge_55() -> None:
    strategy = IQOptionHourOfDayStrategy(min_observations=60, probability_threshold=Decimal("0.55"))
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD",
        account_id="practice",
        strategy_id=IQOPTION_HOUR_OF_DAY_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    base = datetime(2026, 9, 1, 14, 0, 0, tzinfo=UTC)
    # Create 100 observations at hour 14: 60 bullish, 40 bearish (60% bullish)
    candles = []
    for day in range(20):
        dt = base + timedelta(days=day)
        # 3 bullish, 2 bearish per day at 14h
        candles.append(make_candle(dt + timedelta(minutes=0), True))
        candles.append(make_candle(dt + timedelta(minutes=1), True))
        candles.append(make_candle(dt + timedelta(minutes=2), True))
        candles.append(make_candle(dt + timedelta(minutes=3), False))
        candles.append(make_candle(dt + timedelta(minutes=4), False))

    decision = strategy.evaluate_decision(candles, context)
    assert decision.direction == Direction.CALL
    assert decision.directional_observations == 100
    assert decision.bullish_ratio == Decimal("0.60")
    assert decision.reason_code == "HOUR_CONDITIONAL_BULLISH_EDGE"


def test_hour_of_day_put_when_bearish_ratio_ge_55() -> None:
    strategy = IQOptionHourOfDayStrategy(min_observations=60, probability_threshold=Decimal("0.55"))
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD",
        account_id="practice",
        strategy_id=IQOPTION_HOUR_OF_DAY_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    base = datetime(2026, 9, 1, 14, 0, 0, tzinfo=UTC)
    # Create 100 observations at hour 14: 30 bullish, 70 bearish (70% bearish)
    candles = []
    for day in range(20):
        dt = base + timedelta(days=day)
        candles.append(make_candle(dt + timedelta(minutes=0), False))
        candles.append(make_candle(dt + timedelta(minutes=1), False))
        candles.append(make_candle(dt + timedelta(minutes=2), False))
        candles.append(make_candle(dt + timedelta(minutes=3), False))
        candles.append(make_candle(dt + timedelta(minutes=4), True))

    # Add a final candle in hour 14
    candles.append(make_candle(base + timedelta(days=21), False))

    decision = strategy.evaluate_decision(candles, context)
    assert decision.direction == Direction.PUT
    assert decision.bearish_ratio >= Decimal("0.55")
    assert decision.reason_code == "HOUR_CONDITIONAL_BEARISH_EDGE"
