"""EMA pullback: update EMA from close, then require a touch and close back across it."""

from __future__ import annotations

from decimal import Decimal

from primitives._math import ema
from primitives.base import (
    Candle,
    Category,
    Direction,
    Indicator,
    Output,
    ParamRange,
    decimal_range,
    int_range,
)


class EMAPullback(Indicator):
    category = Category.TRIGGER
    name = "ema_pullback"
    param_spec: dict[str, ParamRange] = {
        "period": int_range(2, 200),
        "tolerance": decimal_range("0", "0.05", "0.001"),
    }

    def __init__(self, period: int = 20, tolerance: Decimal = Decimal("0.002")) -> None:
        if period < 2 or not Decimal(0) <= tolerance <= Decimal("0.05"):
            raise ValueError("parameter is outside param_spec")
        self.period = period
        self.tolerance = tolerance
        self.reset()

    @property
    def warmup_required(self) -> int:
        return self.period

    def reset(self) -> None:
        self._count = 0
        self._ema: Decimal | None = None

    def update(self, candle: Candle) -> Output | None:
        self._ema = ema(self._ema, candle.c, self.period)
        self._count += 1
        if self._count < self.period:
            return None
        allowance = abs(self._ema) * self.tolerance
        touched_from_above = candle.l <= self._ema + allowance and candle.c > self._ema
        touched_from_below = candle.h >= self._ema - allowance and candle.c < self._ema
        direction: Direction = (
            "call" if touched_from_above else "put" if touched_from_below else "none"
        )
        return Output(direction=direction, value=self._ema, meta={"tolerance": allowance})
