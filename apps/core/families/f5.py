"""F5: Quadrant family (Session window regime + Quadrant majority trigger + RSI extreme confirm)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from apps.core.families.base import FamilyStrategyBase
from apps.core.families.primitives.confirm.rsi_extreme import RSIExtreme
from apps.core.families.primitives.regime.session_window import SessionWindow
from apps.core.families.primitives.trigger.quadrant_majority import QuadrantMajority


class F5Quadrant(FamilyStrategyBase):
    family_name = "F5"

    def __init__(
        self,
        strategy_key: str,
        params: dict[str, Any],
        hours_utc: Sequence[int] | None = None,
        asset: str = "",
        timeframe: str = "M1",
    ) -> None:
        super().__init__(strategy_key, params, hours_utc, asset, timeframe)
        quadrant_window = int(params.get("quadrant_window", 3))
        rsi_len = int(params.get("rsi_len", 14))
        rsi_lo = Decimal(str(params.get("rsi_lo", 30)))
        rsi_hi = Decimal(str(params.get("rsi_hi", 70)))

        start_min = (hours_utc[0] * 60) if hours_utc and len(hours_utc) >= 2 else 0
        end_min = (hours_utc[1] * 60) if hours_utc and len(hours_utc) >= 2 else 1440
        if start_min == end_min:
            start_min, end_min = 0, 1440

        self._regime = SessionWindow(start_minute=start_min, end_minute=end_min)
        self._trigger = QuadrantMajority(window=quadrant_window)
        self._confirm = RSIExtreme(period=rsi_len, lower=rsi_lo, upper=rsi_hi)
        self._finalize_warmup()
