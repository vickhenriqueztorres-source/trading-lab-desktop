"""Wilder ADX: smooth TR/+DM/-DM first, then smooth DX in the same input order."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from primitives._math import HUNDRED, ZERO, mean
from primitives.base import Candle, Category, Indicator, Output, ParamRange, int_range


class ADX(Indicator):
    category = Category.REGIME
    name = "adx"
    param_spec: dict[str, ParamRange] = {"period": int_range(2, 100)}

    def __init__(self, period: int = 14) -> None:
        if not 2 <= period <= 100:
            raise ValueError("period is outside param_spec")
        self.period = period
        self.reset()

    @property
    def warmup_required(self) -> int:
        """Return ``2 * period``: one seed candle, period DM/TR values and period DX values."""
        return self.period * 2

    def reset(self) -> None:
        self._previous: Candle | None = None
        self._trs: deque[Decimal] = deque(maxlen=self.period)
        self._plus_dms: deque[Decimal] = deque(maxlen=self.period)
        self._minus_dms: deque[Decimal] = deque(maxlen=self.period)
        self._smoothed_tr: Decimal | None = None
        self._smoothed_plus: Decimal | None = None
        self._smoothed_minus: Decimal | None = None
        self._dx: deque[Decimal] = deque(maxlen=self.period)
        self._adx: Decimal | None = None

    def update(self, candle: Candle) -> Output | None:
        previous = self._previous
        self._previous = candle
        if previous is None:
            return None
        tr = max(
            candle.h - candle.l,
            abs(candle.h - previous.c),
            abs(candle.l - previous.c),
        )
        up = candle.h - previous.h
        down = previous.l - candle.l
        plus_dm = up if up > down and up > ZERO else ZERO
        minus_dm = down if down > up and down > ZERO else ZERO

        if self._smoothed_tr is None:
            self._trs.append(tr)
            self._plus_dms.append(plus_dm)
            self._minus_dms.append(minus_dm)
            if len(self._trs) < self.period:
                return None
            self._smoothed_tr = sum(self._trs, ZERO)
            self._smoothed_plus = sum(self._plus_dms, ZERO)
            self._smoothed_minus = sum(self._minus_dms, ZERO)
        else:
            divisor = Decimal(self.period)
            self._smoothed_tr = self._smoothed_tr - self._smoothed_tr / divisor + tr
            assert self._smoothed_plus is not None and self._smoothed_minus is not None
            self._smoothed_plus = self._smoothed_plus - self._smoothed_plus / divisor + plus_dm
            self._smoothed_minus = self._smoothed_minus - self._smoothed_minus / divisor + minus_dm

        if self._smoothed_tr == ZERO:
            plus_di = minus_di = ZERO
        else:
            assert self._smoothed_plus is not None and self._smoothed_minus is not None
            plus_di = HUNDRED * self._smoothed_plus / self._smoothed_tr
            minus_di = HUNDRED * self._smoothed_minus / self._smoothed_tr
        denominator = plus_di + minus_di
        dx = ZERO if denominator == ZERO else HUNDRED * abs(plus_di - minus_di) / denominator
        if self._adx is None:
            self._dx.append(dx)
            if len(self._dx) < self.period:
                return None
            self._adx = mean(self._dx)
        else:
            self._adx = (self._adx * Decimal(self.period - 1) + dx) / Decimal(self.period)
        return Output(
            direction="none",
            value=self._adx,
            meta={"plus_di": plus_di, "minus_di": minus_di},
        )
