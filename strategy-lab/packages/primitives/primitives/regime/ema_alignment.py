"""EMA alignment: update short, medium and long EMAs, then compare order and long slope."""

from __future__ import annotations

from decimal import Decimal

from primitives._math import ema
from primitives.base import Candle, Category, Direction, Indicator, Output, ParamRange, int_range


class EMAAlignment(Indicator):
    category = Category.REGIME
    name = "ema_alignment"
    param_spec: dict[str, ParamRange] = {
        "short": int_range(2, 100),
        "medium": int_range(3, 150),
        "long": int_range(4, 300),
    }

    def __init__(self, short: int = 5, medium: int = 10, long: int = 20) -> None:
        if not 2 <= short < medium < long <= 300:
            raise ValueError("EMA periods must be strictly increasing")
        self.short = short
        self.medium = medium
        self.long = long
        self.reset()

    @property
    def warmup_required(self) -> int:
        return self.long

    def reset(self) -> None:
        self._count = 0
        self._short_value: Decimal | None = None
        self._medium_value: Decimal | None = None
        self._long_value: Decimal | None = None
        self._previous_long: Decimal | None = None

    def update(self, candle: Candle) -> Output | None:
        previous_long = self._long_value
        self._short_value = ema(self._short_value, candle.c, self.short)
        self._medium_value = ema(self._medium_value, candle.c, self.medium)
        self._long_value = ema(self._long_value, candle.c, self.long)
        self._previous_long = previous_long
        self._count += 1
        if self._count < self.long or previous_long is None:
            return None
        assert self._short_value is not None and self._medium_value is not None
        slope = self._long_value - previous_long
        direction: Direction = "none"
        if self._short_value > self._medium_value > self._long_value and slope > 0:
            direction = "call"
        elif self._short_value < self._medium_value < self._long_value and slope < 0:
            direction = "put"
        return Output(
            direction=direction,
            value=slope,
            meta={
                "short": self._short_value,
                "medium": self._medium_value,
                "long": self._long_value,
            },
        )
