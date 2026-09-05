"""Stochastic cross: compute %K from rolling extremes, SMA %D, then compare consecutive K/D."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from primitives._math import HUNDRED, ZERO, mean
from primitives.base import Candle, Category, Direction, Indicator, Output, ParamRange, int_range


class StochCross(Indicator):
    category = Category.CONFIRM
    name = "stoch_cross"
    param_spec: dict[str, ParamRange] = {
        "k_period": int_range(2, 100),
        "d_period": int_range(2, 20),
    }

    def __init__(self, k_period: int = 14, d_period: int = 3) -> None:
        if k_period < 2 or d_period < 2:
            raise ValueError("stochastic period is outside param_spec")
        self.k_period = k_period
        self.d_period = d_period
        self.reset()

    @property
    def warmup_required(self) -> int:
        """Return ``k_period + d_period - 1`` for the first K/D pair."""
        return self.k_period + self.d_period - 1

    def reset(self) -> None:
        self._candles: deque[Candle] = deque(maxlen=self.k_period)
        self._k_values: deque[Decimal] = deque(maxlen=self.d_period)
        self._previous_k: Decimal | None = None
        self._previous_d: Decimal | None = None

    def update(self, candle: Candle) -> Output | None:
        self._candles.append(candle)
        if len(self._candles) < self.k_period:
            return None
        lowest = min(item.l for item in self._candles)
        highest = max(item.h for item in self._candles)
        span = highest - lowest
        k_value = Decimal(50) if span == ZERO else HUNDRED * (candle.c - lowest) / span
        self._k_values.append(k_value)
        if len(self._k_values) < self.d_period:
            return None
        d_value = mean(self._k_values)
        direction: Direction = "none"
        if self._previous_k is not None and self._previous_d is not None:
            if self._previous_k <= self._previous_d and k_value > d_value:
                direction = "call"
            elif self._previous_k >= self._previous_d and k_value < d_value:
                direction = "put"
        self._previous_k = k_value
        self._previous_d = d_value
        return Output(direction=direction, value=k_value, meta={"d": d_value})
