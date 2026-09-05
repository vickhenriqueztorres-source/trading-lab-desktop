"""Wilder RSI extreme: smooth gains/losses canonically, then compare lower before upper."""

from __future__ import annotations

from decimal import Decimal

from primitives._rsi import WilderRSI
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


class RSIExtreme(Indicator):
    category = Category.CONFIRM
    name = "rsi_extreme"
    param_spec: dict[str, ParamRange] = {
        "period": int_range(2, 100),
        "lower": decimal_range("1", "49", "1"),
        "upper": decimal_range("51", "99", "1"),
    }

    def __init__(
        self,
        period: int = 14,
        lower: Decimal = Decimal(30),
        upper: Decimal = Decimal(70),
    ) -> None:
        if period < 2 or not Decimal(1) <= lower < Decimal(50) < upper <= Decimal(99):
            raise ValueError("RSI parameters are outside param_spec")
        self.period = period
        self.lower = lower
        self.upper = upper
        self._rsi = WilderRSI(period)

    @property
    def warmup_required(self) -> int:
        """Return ``period + 1`` closes for the initial Wilder RSI."""
        return self.period + 1

    def reset(self) -> None:
        self._rsi.reset()

    def update(self, candle: Candle) -> Output | None:
        value = self._rsi.update(candle.c)
        if value is None:
            return None
        direction: Direction = (
            "call" if value < self.lower else "put" if value > self.upper else "none"
        )
        return Output(
            direction=direction, value=value, meta={"lower": self.lower, "upper": self.upper}
        )
