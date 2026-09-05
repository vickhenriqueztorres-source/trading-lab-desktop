"""BB outside trigger: calculate population bands from prior closes, then compare current close."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from apps.core.families.primitives._math import mean, population_std
from apps.core.families.primitives.base import (
    Candle,
    Category,
    Direction,
    Indicator,
    Output,
    ParamRange,
    decimal_range,
    int_range,
)


class BBCloseOutside(Indicator):
    category = Category.TRIGGER
    name = "bb_close_outside"
    param_spec: dict[str, ParamRange] = {
        "length": int_range(2, 200),
        "k": decimal_range("0.5", "5", "0.1"),
    }

    def __init__(self, length: int = 20, k: Decimal = Decimal("2")) -> None:
        if length < 2 or not Decimal("0.5") <= k <= Decimal(5):
            raise ValueError("parameter is outside param_spec")
        self.length = length
        self.k = k
        self.reset()

    @property
    def warmup_required(self) -> int:
        """Return ``length + 1``: N prior closes plus the compared close."""
        return self.length + 1

    def reset(self) -> None:
        self._closes: deque[Decimal] = deque(maxlen=self.length)

    def update(self, candle: Candle) -> Output | None:
        if len(self._closes) < self.length:
            self._closes.append(candle.c)
            return None
        middle = mean(self._closes)
        deviation = population_std(self._closes)
        lower = middle - self.k * deviation
        upper = middle + self.k * deviation
        direction: Direction = "call" if candle.c < lower else "put" if candle.c > upper else "none"
        self._closes.append(candle.c)
        return Output(direction=direction, value=candle.c, meta={"lower": lower, "upper": upper})
