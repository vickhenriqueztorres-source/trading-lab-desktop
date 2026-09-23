from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies.iqoption_liquidity_gap import (
    IQOPTION_LIQUIDITY_GAP_STRATEGY_ID,
    IQOptionLiquidityGapStrategy,
    iqoption_liquidity_gap_manifest,
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
    manifest = iqoption_liquidity_gap_manifest()
    assert manifest.strategy_id == IQOPTION_LIQUIDITY_GAP_STRATEGY_ID
    assert manifest.version == "1.1.0"
    assert manifest.warmup_candles == 2
    assert manifest.supported_timeframes == (60,)


def test_liquidity_gap_call_sweep_low_and_full_recovery() -> None:
    strategy = IQOptionLiquidityGapStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD-OTC",
        account_id="practice",
        strategy_id=IQOPTION_LIQUIDITY_GAP_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # Prev candle: Low 1.0450, High 1.0550
    # Sweep threshold low: 1.0450 * 0.999 = 1.043955
    # Curr candle sweeps below 1.043955, closes bullish and recovers above prev high
    prev = make_candle(
        open_price="1.0500", high_price="1.0550", low_price="1.0450", close_price="1.0480"
    )
    curr = make_candle(
        open_price="1.0470",
        high_price="1.0570",
        low_price="1.0430",
        close_price="1.0560",
        open_time=NOW + timedelta(seconds=60),
    )

    decision = strategy.evaluate_decision([prev, curr], context)
    assert decision.direction == Direction.CALL
    assert decision.duration_candles == 2
    assert decision.recovered_fully is True
    assert decision.reason_code == "CALL_SWEEP_LOW_RECOVERED"
    assert decision.sweep_pct > Decimal("0.001")


def test_liquidity_gap_put_sweep_high_and_full_recovery() -> None:
    strategy = IQOptionLiquidityGapStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD-OTC",
        account_id="practice",
        strategy_id=IQOPTION_LIQUIDITY_GAP_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # Prev candle: Low 1.0450, High 1.0550
    # Sweep threshold high: 1.0550 * 1.001 = 1.056055
    # Curr candle sweeps above 1.056055, closes bearish and recovers below prev low
    prev = make_candle(
        open_price="1.0480", high_price="1.0550", low_price="1.0450", close_price="1.0520"
    )
    curr = make_candle(
        open_price="1.0530",
        high_price="1.0570",
        low_price="1.0430",
        close_price="1.0440",
        open_time=NOW + timedelta(seconds=60),
    )

    decision = strategy.evaluate_decision([prev, curr], context)
    assert decision.direction == Direction.PUT
    assert decision.duration_candles == 2
    assert decision.recovered_fully is True
    assert decision.reason_code == "PUT_SWEEP_HIGH_RECOVERED"
    assert decision.sweep_pct > Decimal("0.001")


def test_liquidity_gap_incomplete_recovery_yields_none() -> None:
    strategy = IQOptionLiquidityGapStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD-OTC",
        account_id="practice",
        strategy_id=IQOPTION_LIQUIDITY_GAP_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # Sweeps low but doesn't recover above prev low
    prev = make_candle(
        open_price="1.0500", high_price="1.0550", low_price="1.0450", close_price="1.0480"
    )
    curr = make_candle(
        open_price="1.0435",
        high_price="1.0445",
        low_price="1.0430",
        close_price="1.0440",
        open_time=NOW + timedelta(seconds=60),
    )

    decision = strategy.evaluate_decision([prev, curr], context)
    assert decision.direction is None
    assert decision.recovered_fully is False
    assert decision.reason_code == "RECOVERY_INCOMPLETE"


def test_liquidity_gap_no_sweep_yields_none() -> None:
    strategy = IQOptionLiquidityGapStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD-OTC",
        account_id="practice",
        strategy_id=IQOPTION_LIQUIDITY_GAP_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # Inside previous candle range
    prev = make_candle(
        open_price="1.0500", high_price="1.0550", low_price="1.0450", close_price="1.0480"
    )
    curr = make_candle(
        open_price="1.0480",
        high_price="1.0520",
        low_price="1.0460",
        close_price="1.0500",
        open_time=NOW + timedelta(seconds=60),
    )

    decision = strategy.evaluate_decision([prev, curr], context)
    assert decision.direction is None
    assert decision.reason_code == "NO_SWEEP"


def test_liquidity_gap_warmup_error() -> None:
    strategy = IQOptionLiquidityGapStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD-OTC",
        account_id="practice",
        strategy_id=IQOPTION_LIQUIDITY_GAP_STRATEGY_ID,
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )
    single_candle = [make_candle()]
    with pytest.raises(ValueError, match="warming up"):
        strategy.evaluate_decision(single_candle, context)
