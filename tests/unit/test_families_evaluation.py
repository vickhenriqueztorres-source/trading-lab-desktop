"""Unit tests for Strategy Families F1-F5 evaluation (R-BOT-5)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apps.core.families import (
    FAMILY_CLASSES,
    F1Reversal,
    F2Pullback,
    F3LevelRejection,
    F4SqueezeBreak,
    F5Quadrant,
)
from apps.core.families.primitives import Candle
from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies.models import RuntimeContext


def _make_market_candle(
    index: int,
    price: Decimal,
    *,
    base_time: datetime,
    is_closed: bool = True,
    tick_volume: int | None = None,
) -> MarketCandle:
    close_time = base_time + timedelta(seconds=60 * (index + 1))
    open_time = close_time - timedelta(seconds=60)
    return MarketCandle(
        broker=Broker.IQ_OPTION,
        broker_symbol="EURUSD",
        timeframe_seconds=60,
        open_time=open_time,
        close_time=close_time,
        open=price,
        high=price + Decimal("0.0005"),
        low=price - Decimal("0.0005"),
        close=price,
        is_closed=is_closed,
        tick_volume=tick_volume,
    )


def _family_instances():
    return (
        F1Reversal("f1", {}, asset="EURUSD"),
        F2Pullback("f2", {}, asset="EURUSD"),
        F3LevelRejection("f3", {}, asset="EURUSD"),
        F4SqueezeBreak("f4", {}, asset="EURUSD"),
        F5Quadrant("f5", {}, asset="EURUSD"),
    )


def _context(strategy_id: str) -> RuntimeContext:
    return RuntimeContext(
        broker=Broker.IQ_OPTION,
        account_id="demo-account",
        symbol="EURUSD",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        configuration_version="1",
    )


def test_all_five_families_registered() -> None:
    assert len(FAMILY_CLASSES) == 5
    assert set(FAMILY_CLASSES.keys()) == {"F1", "F2", "F3", "F4", "F5"}


def test_f1_reversal_warmup_and_outside_hours() -> None:
    f1 = F1Reversal(
        strategy_key="f1_test",
        params={
            "adx_len": 14,
            "adx_max": "25.0",
            "bb_len": 20,
            "bb_k": "2.0",
            "rsi_len": 14,
            "rsi_lo": "30.0",
            "rsi_hi": "70.0",
        },
        hours_utc=(2, 4),
        asset="EURUSD",
    )
    assert f1.family_name == "F1"
    assert f1.warmup_required > 0
    assert f1.artifact_bytes.startswith(b"BOT_FAMILY:F1:f1_test")

    ctx = RuntimeContext(
        broker=Broker.IQ_OPTION,
        account_id="demo-account",
        symbol="EURUSD",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        strategy_id="f1_test",
        strategy_version="1.0.0",
        configuration_version="1",
    )

    # Outside hours (12:00 UTC) -> should return None immediately
    base_time = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    candles = [_make_market_candle(i, Decimal("1.1000"), base_time=base_time) for i in range(30)]
    assert f1.evaluate(candles, ctx) is None


def test_f2_pullback_instantiation() -> None:
    f2 = F2Pullback(
        strategy_key="f2_test",
        params={
            "ema_short": 5,
            "ema_medium": 10,
            "ema_long": 20,
            "pullback_len": 20,
            "pullback_tolerance": "0.002",
            "body_max": "0.35",
            "wick_min": "0.5",
        },
        asset="EURUSD",
    )
    assert f2.family_name == "F2"
    assert f2.warmup_required == 20
    assert f2.artifact_bytes == b"BOT_FAMILY:F2:f2_test"


def test_f3_level_rejection_instantiation() -> None:
    f3 = F3LevelRejection(
        strategy_key="f3_test",
        params={
            "level_support": "99.0",
            "level_resistance": "101.0",
            "level_tolerance": "0.1",
            "body_max": "0.35",
            "wick_min": "0.5",
        },
        hours_utc=(0, 6),
        asset="EURUSD",
    )
    assert f3.family_name == "F3"
    assert f3.warmup_required >= 1


def test_f4_squeeze_break_instantiation() -> None:
    f4 = F4SqueezeBreak(
        strategy_key="f4_test",
        params={
            "bb_len": 20,
            "bb_k": "2.0",
            "width_median_len": 20,
            "width_ratio_max": "0.8",
            "break_len": 20,
            "volume_len": 20,
            "volume_min": "1.5",
        },
        asset="EURUSD",
    )
    assert f4.family_name == "F4"
    assert f4.warmup_required >= 39


def test_f5_quadrant_instantiation() -> None:
    f5 = F5Quadrant(
        strategy_key="f5_test",
        params={
            "quadrant_window": 3,
            "rsi_len": 14,
            "rsi_lo": "30.0",
            "rsi_hi": "70.0",
        },
        hours_utc=(0, 24),
        asset="EURUSD",
    )
    assert f5.family_name == "F5"
    assert f5.warmup_required >= 15


def test_family_warmup_equals_max_of_components() -> None:
    for family in _family_instances():
        assert family.warmup_required == max(
            family._regime.warmup_required,
            family._trigger.warmup_required,
            family._confirm.warmup_required,
        )


def test_f1_with_20_candles_returns_warming_up_not_none() -> None:
    family = F1Reversal("f1_warmup", {}, asset="EURUSD")
    base_time = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    candles = [
        _make_market_candle(
            i, Decimal("1.1000") + Decimal(i % 3) / Decimal(10_000), base_time=base_time
        )
        for i in range(20)
    ]

    result = family.evaluate_detailed(candles, _context("f1_warmup"))

    assert result.direction is None
    assert result.stage == "WARMING_UP"
    assert (result.warmup_have, result.warmup_need) == (20, 28)


def test_f1_with_31_candles_reaches_ok_or_no_signal() -> None:
    family = F1Reversal("f1_ready", {}, asset="EURUSD")
    base_time = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    candles = [
        _make_market_candle(
            i, Decimal("1.1000") + Decimal(i % 5) / Decimal(10_000), base_time=base_time
        )
        for i in range(31)
    ]

    result = family.evaluate_detailed(candles, _context("f1_ready"))

    assert result.stage in {"OK", "NO_SIGNAL"}
    assert result.regime is not None
    assert result.trigger is not None
    assert result.confirm is not None


def test_f4_without_tick_volume_reports_unavailable() -> None:
    family = F4SqueezeBreak("f4_no_volume", {}, asset="EURUSD")
    base_time = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    candles = [
        _make_market_candle(
            i, Decimal("1.1000") + Decimal(i % 4) / Decimal(10_000), base_time=base_time
        )
        for i in range(42)
    ]

    result = family.evaluate_detailed(candles, _context("f4_no_volume"))

    assert result.stage == "TICK_VOLUME_UNAVAILABLE"
    assert result.direction is None


def test_f4_with_real_tick_volume_evaluates() -> None:
    family = F4SqueezeBreak("f4_volume", {}, asset="EURUSD")
    base_time = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    candles = [
        _make_market_candle(
            i,
            Decimal("1.1000") + Decimal(i % 4) / Decimal(10_000),
            base_time=base_time,
            tick_volume=100 + i,
        )
        for i in range(42)
    ]

    result = family.evaluate_detailed(candles, _context("f4_volume"))

    assert result.stage not in {"WARMING_UP", "TICK_VOLUME_UNAVAILABLE"}
    assert result.regime is not None
    assert result.trigger is not None
    assert result.confirm is not None


def test_evaluate_wrapper_preserves_legacy_behavior() -> None:
    family = F3LevelRejection("f3_wrapper", {}, asset="EURUSD")
    base_time = datetime(2026, 9, 1, 0, 2, tzinfo=UTC)
    candle = replace(
        _make_market_candle(0, Decimal("99.2"), base_time=base_time),
        open=Decimal("99.15"),
        high=Decimal("99.25"),
        low=Decimal("98.95"),
    )
    # Legacy replay consensus must actually emit, not merely compare None == None.
    legacy = family.update_candle(
        Candle(
            ts=int(candle.close_time.timestamp()),
            o=candle.open,
            h=candle.high,
            l=candle.low,
            c=candle.close,
            tick_vol=1,
        )
    )
    detailed = family.evaluate_detailed([candle], _context("f3_wrapper"))
    assert legacy is Direction.CALL
    assert detailed.stage == "OK"
    assert family.evaluate([candle], _context("f3_wrapper")) == detailed.direction == legacy
