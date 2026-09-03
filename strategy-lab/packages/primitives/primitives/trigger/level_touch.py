"""Level touch: compare candle extremes to injected support/resistance with absolute tolerance."""

from __future__ import annotations

from decimal import Decimal

from primitives.base import (
    Candle,
    Category,
    Direction,
    Indicator,
    Output,
    ParamRange,
    decimal_range,
)


class LevelTouch(Indicator):
    category = Category.TRIGGER
    name = "level_touch"
    param_spec: dict[str, ParamRange] = {
        "support": decimal_range("0.00000001", "1000000", "0.00000001"),
        "resistance": decimal_range("0.00000001", "1000000", "0.00000001"),
        "tolerance": decimal_range("0", "10000", "0.00000001"),
    }

    def __init__(
        self,
        support: Decimal = Decimal("99"),
        resistance: Decimal = Decimal("101"),
        tolerance: Decimal = Decimal("0.1"),
    ) -> None:
        if support <= 0 or resistance <= support or tolerance < 0:
            raise ValueError("levels are invalid")
        self.support = support
        self.resistance = resistance
        self.tolerance = tolerance

    @property
    def warmup_required(self) -> int:
        return 1

    def reset(self) -> None:
        return None

    def update(self, candle: Candle) -> Output:
        support_distance = abs(candle.l - self.support)
        resistance_distance = abs(candle.h - self.resistance)
        call = support_distance <= self.tolerance and candle.c >= self.support
        put = resistance_distance <= self.tolerance and candle.c <= self.resistance
        direction: Direction = "call" if call and not put else "put" if put and not call else "none"
        return Output(
            direction=direction,
            value=min(support_distance, resistance_distance),
            meta={"support_distance": support_distance, "resistance_distance": resistance_distance},
        )
