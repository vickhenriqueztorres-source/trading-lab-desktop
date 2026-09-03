"""F3: Level rejection family.
Session window regime + Level touch trigger + Candle rejection confirm.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from apps.core.families.base import FamilyStrategyBase
from apps.core.families.primitives.confirm.candle_rejection import CandleRejection
from apps.core.families.primitives.regime.session_window import SessionWindow
from apps.core.families.primitives.trigger.level_touch import LevelTouch


class F3LevelRejection(FamilyStrategyBase):
    family_name = "F3"

    def __init__(
        self,
        strategy_key: str,
        params: dict[str, Any],
        hours_utc: Sequence[int] | None = None,
        asset: str = "",
        timeframe: str = "M1",
    ) -> None:
        super().__init__(strategy_key, params, hours_utc, asset, timeframe)
        support = Decimal(str(params.get("level_support", "99")))
        resistance = Decimal(str(params.get("level_resistance", "101")))
        tolerance = Decimal(str(params.get("level_tolerance", "0.1")))
        body_max = Decimal(str(params.get("body_max", "0.35")))
        wick_min = Decimal(str(params.get("wick_min", "0.5")))

        start_min = (hours_utc[0] * 60) if hours_utc and len(hours_utc) >= 2 else 0
        end_min = (hours_utc[1] * 60) if hours_utc and len(hours_utc) >= 2 else 1440
        if start_min == end_min:
            start_min, end_min = 0, 1440

        self._regime = SessionWindow(start_minute=start_min, end_minute=end_min)
        self._trigger = LevelTouch(support=support, resistance=resistance, tolerance=tolerance)
        self._confirm = CandleRejection(max_body_ratio=body_max, min_wick_ratio=wick_min)
        self._warmup_required = max(
            self._regime.warmup_required,
            self._trigger.warmup_required,
            self._confirm.warmup_required,
        )
