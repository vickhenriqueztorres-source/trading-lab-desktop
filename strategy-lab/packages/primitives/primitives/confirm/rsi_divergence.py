"""RSI divergence: compare current price/RSI with values exactly lookback observations earlier."""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from primitives._rsi import WilderRSI
from primitives.base import Candle, Category, Direction, Indicator, Output, ParamRange, int_range


class RSIDivergence(Indicator):
    category = Category.CONFIRM
    name = "rsi_divergence"
    param_spec: dict[str, ParamRange] = {
        "period": int_range(2, 100),
        "lookback": int_range(2, 100),
    }

    def __init__(self, period: int = 14, lookback: int = 5) -> None:
        if period < 2 or lookback < 2:
            raise ValueError("RSI divergence period is outside param_spec")
        self.period = period
        self.lookback = lookback
        self._rsi = WilderRSI(period)
        self._observations: deque[tuple[Decimal, Decimal]] = deque(maxlen=lookback + 1)

    @property
    def warmup_required(self) -> int:
        return self.period + self.lookback + 1

    def reset(self) -> None:
        self._rsi.reset()
        self._observations.clear()

    def update(self, candle: Candle) -> Output | None:
        rsi = self._rsi.update(candle.c)
        if rsi is None:
            return None
        self._observations.append((candle.c, rsi))
        if len(self._observations) <= self.lookback:
            return None
        old_price, old_rsi = self._observations[0]
        direction: Direction = "none"
        if candle.c < old_price and rsi > old_rsi:
            direction = "call"
        elif candle.c > old_price and rsi < old_rsi:
            direction = "put"
        return Output(
            direction=direction,
            value=rsi - old_rsi,
            meta={"price_change": candle.c - old_price, "rsi_change": rsi - old_rsi},
        )
