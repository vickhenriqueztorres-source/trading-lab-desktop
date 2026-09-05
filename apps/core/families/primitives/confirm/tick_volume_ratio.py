"""Tick-volume ratio: compare current volume with the mean of the prior N volumes."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from apps.core.families.primitives._math import ZERO, direction_from_body, mean
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


class TickVolumeRatio(Indicator):
    category = Category.CONFIRM
    name = "tick_volume_ratio"
    requires_tick_volume = True
    param_spec: dict[str, ParamRange] = {
        "length": int_range(2, 200),
        "minimum_ratio": decimal_range("0.5", "5", "0.1"),
    }

    def __init__(self, length: int = 20, minimum_ratio: Decimal = Decimal("1.5")) -> None:
        if length < 2 or not Decimal("0.5") <= minimum_ratio <= Decimal(5):
            raise ValueError("parameter is outside param_spec")
        self.length = length
        self.minimum_ratio = minimum_ratio
        self.reset()

    @property
    def warmup_required(self) -> int:
        """Return ``length + 1``: N baseline volumes plus the compared candle."""
        return self.length + 1

    def reset(self) -> None:
        self._volumes: deque[Decimal] = deque(maxlen=self.length)

    def update(self, candle: Candle) -> Output | None:
        if candle.tick_vol is None:
            return None
        if len(self._volumes) < self.length:
            self._volumes.append(Decimal(candle.tick_vol))
            return None
        baseline = mean(self._volumes)
        ratio = ZERO if baseline == ZERO else Decimal(candle.tick_vol) / baseline
        direction: Direction = (
            direction_from_body(candle.o, candle.c) if ratio >= self.minimum_ratio else "none"
        )
        self._volumes.append(Decimal(candle.tick_vol))
        return Output(direction=direction, value=ratio, meta={"baseline": baseline})
