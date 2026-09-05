"""Candle rejection: normalize body and both wicks by range, then choose the dominant wick."""

from __future__ import annotations

from decimal import Decimal

from apps.core.families.primitives._math import ZERO
from apps.core.families.primitives.base import (
    Candle,
    Category,
    Direction,
    Indicator,
    Output,
    ParamRange,
    decimal_range,
)


class CandleRejection(Indicator):
    category = Category.CONFIRM
    name = "candle_rejection"
    param_spec: dict[str, ParamRange] = {
        "max_body_ratio": decimal_range("0.05", "0.8", "0.05"),
        "min_wick_ratio": decimal_range("0.2", "0.9", "0.05"),
    }

    def __init__(
        self,
        max_body_ratio: Decimal = Decimal("0.35"),
        min_wick_ratio: Decimal = Decimal("0.5"),
    ) -> None:
        if not Decimal("0.05") <= max_body_ratio <= Decimal("0.8"):
            raise ValueError("max_body_ratio is outside param_spec")
        if not Decimal("0.2") <= min_wick_ratio <= Decimal("0.9"):
            raise ValueError("min_wick_ratio is outside param_spec")
        self.max_body_ratio = max_body_ratio
        self.min_wick_ratio = min_wick_ratio

    @property
    def warmup_required(self) -> int:
        """Return one candle because body and wick ratios are candle-local."""
        return 1

    def reset(self) -> None:
        return None

    def update(self, candle: Candle) -> Output:
        span = candle.h - candle.l
        if span == ZERO:
            return Output(direction="none", value=ZERO, meta={"body_ratio": ZERO})
        body_ratio = abs(candle.c - candle.o) / span
        lower_wick = min(candle.o, candle.c) - candle.l
        upper_wick = candle.h - max(candle.o, candle.c)
        lower_ratio = lower_wick / span
        upper_ratio = upper_wick / span
        direction: Direction = "none"
        if body_ratio <= self.max_body_ratio:
            if lower_ratio >= self.min_wick_ratio and lower_ratio > upper_ratio:
                direction = "call"
            elif upper_ratio >= self.min_wick_ratio and upper_ratio > lower_ratio:
                direction = "put"
        return Output(
            direction=direction,
            value=max(lower_ratio, upper_ratio),
            meta={
                "body_ratio": body_ratio,
                "lower_wick_ratio": lower_ratio,
                "upper_wick_ratio": upper_ratio,
            },
        )
