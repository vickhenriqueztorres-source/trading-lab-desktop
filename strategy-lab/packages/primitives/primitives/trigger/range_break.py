"""Range break: compare current close with the high/low envelope of the prior N candles."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from primitives.base import Candle, Category, Direction, Indicator, Output, ParamRange, int_range


class RangeBreak(Indicator):
    category = Category.TRIGGER
    name = "range_break"
    param_spec: dict[str, ParamRange] = {"length": int_range(2, 200)}

    def __init__(self, length: int = 20) -> None:
        if not 2 <= length <= 200:
            raise ValueError("length is outside param_spec")
        self.length = length
        self.reset()

    @property
    def warmup_required(self) -> int:
        return self.length + 1

    def reset(self) -> None:
        self._candles: deque[Candle] = deque(maxlen=self.length)

    def update(self, candle: Candle) -> Output | None:
        if len(self._candles) < self.length:
            self._candles.append(candle)
            return None
        upper = max(item.h for item in self._candles)
        lower = min(item.l for item in self._candles)
        direction: Direction = "call" if candle.c > upper else "put" if candle.c < lower else "none"
        self._candles.append(candle)
        distance = (
            candle.c - upper
            if direction == "call"
            else lower - candle.c
            if direction == "put"
            else Decimal(0)
        )
        return Output(direction=direction, value=distance, meta={"upper": upper, "lower": lower})
