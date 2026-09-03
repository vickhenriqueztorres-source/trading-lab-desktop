"""Incremental Wilder RSI state used by confirm indicators."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from apps.core.families.primitives._math import HUNDRED, ZERO, mean


class WilderRSI:
    def __init__(self, period: int) -> None:
        self.period = period
        self.reset()

    def reset(self) -> None:
        self._previous: Decimal | None = None
        self._gains: deque[Decimal] = deque(maxlen=self.period)
        self._losses: deque[Decimal] = deque(maxlen=self.period)
        self._average_gain: Decimal | None = None
        self._average_loss: Decimal | None = None

    def update(self, close: Decimal) -> Decimal | None:
        previous = self._previous
        self._previous = close
        if previous is None:
            return None
        change = close - previous
        gain = max(change, ZERO)
        loss = max(-change, ZERO)
        if self._average_gain is None or self._average_loss is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) < self.period:
                return None
            self._average_gain = mean(self._gains)
            self._average_loss = mean(self._losses)
        else:
            divisor = Decimal(self.period)
            self._average_gain = (self._average_gain * Decimal(self.period - 1) + gain) / divisor
            self._average_loss = (self._average_loss * Decimal(self.period - 1) + loss) / divisor
        if self._average_loss == ZERO:
            return HUNDRED if self._average_gain > ZERO else Decimal(50)
        relative_strength = self._average_gain / self._average_loss
        return HUNDRED - HUNDRED / (Decimal(1) + relative_strength)
