from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies.iqoption_extreme_rejection import (
    IQOPTION_EXTREME_REJECTION_STRATEGY_ID,
    IQOptionExtremeRejectionStrategy,
    iqoption_extreme_rejection_manifest,
)
from packages.strategies.models import RuntimeContext

NOW = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)


def make_candle(
    open_price: str,
    high_price: str,
    low_price: str,
    close_price: str,
    minute_offset: int = 0,
    symbol: str = "EURUSD",
) -> MarketCandle:
    ot = NOW + timedelta(minutes=minute_offset)
    return MarketCandle(
        broker=Broker.IQ_OPTION,
        broker_symbol=symbol,
        timeframe_seconds=60,
        open_time=ot,
        close_time=ot + timedelta(seconds=60),
        open=Decimal(open_price),
        high=Decimal(high_price),
        low=Decimal(low_price),
        close=Decimal(close_price),
        is_closed=True,
    )


def test_extreme_rejection_manifest() -> None:
    manifest = iqoption_extreme_rejection_manifest()
    assert manifest.strategy_id == IQOPTION_EXTREME_REJECTION_STRATEGY_ID
    assert manifest.version == "1.0.0"
    assert manifest.warmup_candles == 35
    assert manifest.supported_timeframes == (60,)


def test_extreme_rejection_call_signal() -> None:
    strategy = IQOptionExtremeRejectionStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD",
        account_id="practice",
        strategy_id=IQOPTION_EXTREME_REJECTION_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # 26 base range-bound candles: range ~ 0.0030
    candles: list[MarketCandle] = []
    for i in range(26):
        candles.append(make_candle("1.0500", "1.0515", "1.0485", "1.0505", i))

    # Preceding 8 candles (index 26 to 33, t-8 to t-1):
    # min8 will be 1.0490, max8 will be 1.0540
    for i in range(26, 34):
        candles.append(make_candle("1.0510", "1.0535", "1.0490", "1.0520", i))

    # Candle 34 (candle t):
    # A20 is approx 0.0030 -> penetration 0.10 * A20 = 0.0003
    # min8 = 1.0490. Target low < 1.0490 - 0.0003 = 1.0487.
    # Set low = 1.0480 (< 1.0487).
    # Return close > min8: close = 1.0515 (> 1.0490).
    # Open = 1.0500, High = 1.0520.
    # Amplitude = 1.0520 - 1.0480 = 0.0040.
    # Lower wick = min(1.0500, 1.0515) - 1.0480 = 1.0500 - 1.0480 = 0.0020.
    # Lower wick ratio = 0.0020 / 0.0040 = 0.50 (>= 0.35).
    # Close pos = (1.0515 - 1.0480) / 0.0040 = 0.0035 / 0.0040 = 0.875 (>= 0.65).
    candles.append(make_candle("1.0500", "1.0520", "1.0480", "1.0515", 34))

    assert len(candles) == 35
    decision = strategy.evaluate_decision(candles, context)
    assert decision.direction == Direction.CALL
    assert decision.min8 == Decimal("1.0490")
    assert decision.lower_wick_ratio >= Decimal("0.35")
    assert decision.pos_close >= Decimal("0.65")
    assert decision.duration_candles == 1
    assert decision.reason_code == "EXTREME_REJECTION_SWEEP_CALL"


def test_extreme_rejection_put_signal() -> None:
    strategy = IQOptionExtremeRejectionStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD",
        account_id="practice",
        strategy_id=IQOPTION_EXTREME_REJECTION_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    candles: list[MarketCandle] = []
    for i in range(26):
        candles.append(make_candle("1.0500", "1.0515", "1.0485", "1.0505", i))

    # Preceding 8 candles: max8 = 1.0540, min8 = 1.0490
    for i in range(26, 34):
        candles.append(make_candle("1.0510", "1.0540", "1.0495", "1.0520", i))

    # Candle 34 (candle t):
    # max8 = 1.0540. Target high > 1.0540 + 0.0003 = 1.0543.
    # Set high = 1.0550 (> 1.0543).
    # Return close < max8: close = 1.0505 (< 1.0540).
    # Open = 1.0520, Low = 1.0500.
    # Amplitude = 1.0550 - 1.0500 = 0.0050.
    # Upper wick = 1.0550 - max(1.0520, 1.0505) = 1.0550 - 1.0520 = 0.0030.
    # Upper wick ratio = 0.0030 / 0.0050 = 0.60 (>= 0.35).
    # Close pos = (1.0505 - 1.0500) / 0.0050 = 0.0005 / 0.0050 = 0.10 (<= 0.35).
    candles.append(make_candle("1.0520", "1.0550", "1.0500", "1.0505", 34))

    assert len(candles) == 35
    decision = strategy.evaluate_decision(candles, context)
    assert decision.direction == Direction.PUT
    assert decision.max8 == Decimal("1.0540")
    assert decision.upper_wick_ratio >= Decimal("0.35")
    assert decision.pos_close <= Decimal("0.35")
    assert decision.duration_candles == 1
    assert decision.reason_code == "EXTREME_REJECTION_SWEEP_PUT"


def test_extreme_rejection_exclusion_trend_too_strong() -> None:
    strategy = IQOptionExtremeRejectionStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD",
        account_id="practice",
        strategy_id=IQOPTION_EXTREME_REJECTION_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # Steep upward trend causing |EMA10 - EMA30| to blow out past 0.50 * A20
    candles: list[MarketCandle] = []
    base = Decimal("1.0000")
    for i in range(34):
        o = base + Decimal(i) * Decimal("0.0030")
        c = o + Decimal("0.0020")
        h = c + Decimal("0.0005")
        l = o - Decimal("0.0005")
        candles.append(make_candle(str(o), str(h), str(l), str(c), i))

    # Candle 34 tries to sweep high
    candles.append(make_candle("1.1020", "1.1100", "1.1010", "1.1015", 34))

    decision = strategy.evaluate_decision(candles, context)
    assert decision.direction is None
    assert decision.reason_code == "EXCLUSION_TREND_TOO_STRONG"
