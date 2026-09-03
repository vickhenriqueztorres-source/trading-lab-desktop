"""Canonical Decimal helpers shared by indicator implementations."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from primitives.base import Direction

ZERO = Decimal(0)
ONE = Decimal(1)
TWO = Decimal(2)
HUNDRED = Decimal(100)


def mean(values: Iterable[Decimal]) -> Decimal:
    items = tuple(values)
    if not items:
        raise ValueError("mean requires at least one value")
    return sum(items, ZERO) / Decimal(len(items))


def population_std(values: Iterable[Decimal]) -> Decimal:
    items = tuple(values)
    average = mean(items)
    variance = sum(((item - average) ** 2 for item in items), ZERO) / Decimal(len(items))
    return variance.sqrt()


def median(values: Iterable[Decimal]) -> Decimal:
    items = sorted(values)
    if not items:
        raise ValueError("median requires at least one value")
    middle = len(items) // 2
    if len(items) % 2:
        return items[middle]
    return (items[middle - 1] + items[middle]) / TWO


def ema(previous: Decimal | None, value: Decimal, period: int) -> Decimal:
    if previous is None:
        return value
    alpha = TWO / Decimal(period + 1)
    return previous + alpha * (value - previous)


def direction_from_body(candle_open: Decimal, candle_close: Decimal) -> Direction:
    if candle_close > candle_open:
        return "call"
    if candle_close < candle_open:
        return "put"
    return "none"
