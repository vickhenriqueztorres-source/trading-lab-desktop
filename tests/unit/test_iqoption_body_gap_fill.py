from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies.iqoption_body_gap_fill import (
    IQOPTION_BODY_GAP_FILL_STRATEGY_ID,
    IQOptionBodyGapFillStrategy,
    iqoption_body_gap_fill_manifest,
)
from packages.strategies.models import RuntimeContext

NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=UTC)


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


def test_body_gap_fill_manifest() -> None:
    manifest = iqoption_body_gap_fill_manifest()
    assert manifest.strategy_id == IQOPTION_BODY_GAP_FILL_STRATEGY_ID
    assert manifest.version == "1.0.0"
    assert manifest.warmup_candles == 16


def test_body_gap_fill_gap_up_generates_put() -> None:
    strategy = IQOptionBodyGapFillStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD",
        account_id="practice",
        strategy_id=IQOPTION_BODY_GAP_FILL_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # 15 baseline candles with regular range ~0.0010 (ATR ~ 0.0010)
    candles = [make_candle("1.0800", "1.0810", "1.0790", "1.0805", i) for i in range(15)]
    # Candle 15: body between 1.0800 and 1.0805 (max body = 1.0805)
    candles.append(make_candle("1.0800", "1.0806", "1.0795", "1.0805", 15))

    # Candle 16 (gap up): open 1.0820, close 1.0825 (min body = 1.0820)
    # Gap separation = 1.0820 - 1.0805 = 0.0015 >= 0.25 * ATR (~0.00025)
    # Midpoint = (1.0805 + 1.0820) / 2 = 1.08125
    # Close is 1.0825 > 1.08125 -> Unfilled -> PUT
    candles.append(make_candle("1.0820", "1.0826", "1.0819", "1.0825", 16))

    decision = strategy.evaluate_decision(candles, context)
    assert decision.direction == Direction.PUT
    assert decision.bars_since_gap == 1
    assert decision.reason_code == "BODY_GAP_UP_FILL_PUT"
    assert decision.gap_separation >= Decimal("0.0005")


def test_body_gap_fill_gap_down_generates_call() -> None:
    strategy = IQOptionBodyGapFillStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD",
        account_id="practice",
        strategy_id=IQOPTION_BODY_GAP_FILL_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    candles = [make_candle("1.0800", "1.0810", "1.0790", "1.0805", i) for i in range(15)]
    # Candle 15: body between 1.0800 and 1.0805 (min body = 1.0800)
    candles.append(make_candle("1.0800", "1.0806", "1.0795", "1.0805", 15))

    # Candle 16 (gap down): open 1.0780, close 1.0775 (max body = 1.0780)
    # Gap separation = 1.0800 - 1.0780 = 0.0020 >= 0.25 * ATR
    # Midpoint = (1.0780 + 1.0800) / 2 = 1.0790
    # Close is 1.0775 < 1.0790 -> Unfilled -> CALL
    candles.append(make_candle("1.0780", "1.0781", "1.0774", "1.0775", 16))

    decision = strategy.evaluate_decision(candles, context)
    assert decision.direction == Direction.CALL
    assert decision.bars_since_gap == 1
    assert decision.reason_code == "BODY_GAP_DOWN_FILL_CALL"
