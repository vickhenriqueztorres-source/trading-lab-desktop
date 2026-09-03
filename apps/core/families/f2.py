"""F2: Pullback family (EMA alignment regime + EMA pullback trigger + Candle rejection confirm)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from apps.core.families.base import FamilyStrategyBase
from apps.core.families.primitives.confirm.candle_rejection import CandleRejection
from apps.core.families.primitives.regime.ema_alignment import EMAAlignment
from apps.core.families.primitives.trigger.ema_pullback import EMAPullback


class F2Pullback(FamilyStrategyBase):
    family_name = "F2"

    def __init__(
        self,
        strategy_key: str,
        params: dict[str, Any],
        hours_utc: Sequence[int] | None = None,
        asset: str = "",
        timeframe: str = "M1",
    ) -> None:
        super().__init__(strategy_key, params, hours_utc, asset, timeframe)
        ema_short = int(params.get("ema_short", 5))
        ema_medium = int(params.get("ema_medium", 10))
        ema_long = int(params.get("ema_long", 20))
        pullback_len = int(params.get("pullback_len", 20))
        pullback_tolerance = Decimal(str(params.get("pullback_tolerance", "0.002")))
        body_max = Decimal(str(params.get("body_max", "0.35")))
        wick_min = Decimal(str(params.get("wick_min", "0.5")))

        self._regime = EMAAlignment(short=ema_short, medium=ema_medium, long=ema_long)
        self._trigger = EMAPullback(period=pullback_len, tolerance=pullback_tolerance)
        self._confirm = CandleRejection(max_body_ratio=body_max, min_wick_ratio=wick_min)
        self._warmup_required = max(
            self._regime.warmup_required,
            self._trigger.warmup_required,
            self._confirm.warmup_required,
        )
