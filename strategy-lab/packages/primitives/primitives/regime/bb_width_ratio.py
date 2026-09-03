"""BB width ratio: population bands, then current width divided by rolling median width."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from primitives._math import ZERO, mean, median, population_std
from primitives.base import (
    Candle,
    Category,
    Indicator,
    Output,
    ParamRange,
    decimal_range,
    int_range,
)


class BBWidthRatio(Indicator):
    category = Category.REGIME
    name = "bb_width_ratio"
    param_spec: dict[str, ParamRange] = {
        "length": int_range(2, 200),
        "median_length": int_range(2, 200),
        "k": decimal_range("0.5", "5", "0.1"),
    }

    def __init__(
        self, length: int = 20, median_length: int = 20, k: Decimal = Decimal("2")
    ) -> None:
        if length < 2 or median_length < 2 or not Decimal("0.5") <= k <= Decimal(5):
            raise ValueError("parameter is outside param_spec")
        self.length = length
        self.median_length = median_length
        self.k = k
        self.reset()

    @property
    def warmup_required(self) -> int:
        return self.length + self.median_length - 1

    def reset(self) -> None:
        self._closes: deque[Decimal] = deque(maxlen=self.length)
        self._widths: deque[Decimal] = deque(maxlen=self.median_length)

    def update(self, candle: Candle) -> Output | None:
        self._closes.append(candle.c)
        if len(self._closes) < self.length:
            return None
        middle = mean(self._closes)
        deviation = population_std(self._closes)
        width = ZERO if middle == ZERO else (self.k * deviation * Decimal(2)) / abs(middle)
        self._widths.append(width)
        if len(self._widths) < self.median_length:
            return None
        baseline = median(self._widths)
        ratio = ZERO if baseline == ZERO else width / baseline
        return Output(
            direction="none", value=ratio, meta={"width": width, "median_width": baseline}
        )
