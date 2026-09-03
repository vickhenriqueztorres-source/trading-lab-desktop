"""Quadrant majority: count candle bodies only in minutes 2–4 and 7–9 of each ten-minute block."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from primitives._math import direction_from_body
from primitives.base import Candle, Category, Direction, Indicator, Output, ParamRange, int_range


class QuadrantMajority(Indicator):
    category = Category.TRIGGER
    name = "quadrant_majority"
    param_spec: dict[str, ParamRange] = {"window": int_range(3, 21, 2)}

    def __init__(self, window: int = 3) -> None:
        if not 3 <= window <= 21 or window % 2 == 0:
            raise ValueError("window must be odd and inside param_spec")
        self.window = window
        self.reset()

    @property
    def warmup_required(self) -> int:
        return self.window

    def reset(self) -> None:
        self._bodies: deque[str] = deque(maxlen=self.window)

    def update(self, candle: Candle) -> Output | None:
        minute_in_block = candle.ts // 60 % 10
        if minute_in_block not in {2, 3, 4, 7, 8, 9}:
            return Output(direction="none", value=Decimal(0), meta={"eligible": Decimal(0)})
        self._bodies.append(direction_from_body(candle.o, candle.c))
        if len(self._bodies) < self.window:
            return None
        calls = self._bodies.count("call")
        puts = self._bodies.count("put")
        direction: Direction = "call" if calls > puts else "put" if puts > calls else "none"
        return Output(
            direction=direction,
            value=Decimal(calls - puts),
            meta={"calls": Decimal(calls), "puts": Decimal(puts), "eligible": Decimal(1)},
        )
