"""Deterministic indicator calculations for IQ Option strategies.

All calculations use pure Decimal arithmetic to avoid IEEE-754 floating-point
distortions in financial boundary tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from packages.domain.market import MarketCandle


def calculate_ema(
    values: Sequence[Decimal],
    period: int,
) -> Decimal:
    """Calculate the Exponential Moving Average (EMA) using standard smoothing."""
    if period <= 0:
        raise ValueError("EMA period must be positive")
    if len(values) < period:
        raise ValueError(f"EMA requires at least {period} values (received {len(values)})")
    if any(not v.is_finite() or v <= 0 for v in values):
        raise ValueError("EMA values must be positive finite decimals")

    # Initial SMA over first `period` items
    ema = sum(values[:period]) / Decimal(period)
    multiplier = Decimal(2) / Decimal(period + 1)
    one_minus_mult = Decimal(1) - multiplier

    for val in values[period:]:
        ema = (val * multiplier) + (ema * one_minus_mult)

    return ema


def calculate_average_range(
    candles: Sequence[MarketCandle],
    period: int = 20,
) -> Decimal:
    """Calculate the average candle range (High - Low) over the last `period` candles."""
    if period <= 0:
        raise ValueError("Average range period must be positive")
    if len(candles) < period:
        raise ValueError(
            f"Average range requires at least {period} candles (received {len(candles)})"
        )

    selected = candles[-period:]
    ranges = [c.high - c.low for c in selected]
    return sum(ranges) / Decimal(period)
