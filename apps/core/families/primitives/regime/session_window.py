"""UTC session window: derive minute-of-day from epoch and test the half-open interval."""

from __future__ import annotations

from decimal import Decimal

from apps.core.families.primitives.base import (
    Candle,
    Category,
    Indicator,
    Output,
    ParamRange,
    int_range,
)


class SessionWindow(Indicator):
    category = Category.REGIME
    name = "session_window"
    param_spec: dict[str, ParamRange] = {
        "start_minute": int_range(0, 1439),
        "end_minute": int_range(1, 1440),
    }

    def __init__(self, start_minute: int = 0, end_minute: int = 360) -> None:
        if not 0 <= start_minute <= 1439 or not 1 <= end_minute <= 1440:
            raise ValueError("session minute is outside param_spec")
        if start_minute == end_minute:
            raise ValueError("session window cannot be empty")
        self.start_minute = start_minute
        self.end_minute = end_minute

    @property
    def warmup_required(self) -> int:
        """Return one candle because the UTC session predicate is stateless."""
        return 1

    def reset(self) -> None:
        return None

    def update(self, candle: Candle) -> Output:
        minute = candle.ts % 86400 // 60
        if self.start_minute < self.end_minute:
            active = self.start_minute <= minute < self.end_minute
        else:
            active = minute >= self.start_minute or minute < self.end_minute
        return Output(
            direction="none",
            value=Decimal(1 if active else 0),
            meta={"minute_utc": Decimal(minute)},
        )
