"""F4: Squeeze break family.
BB width ratio regime + Range break trigger + Tick volume ratio confirm.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from apps.core.families.base import FamilyStrategyBase
from apps.core.families.primitives.base import Output
from apps.core.families.primitives.confirm.tick_volume_ratio import TickVolumeRatio
from apps.core.families.primitives.regime.bb_width_ratio import BBWidthRatio
from apps.core.families.primitives.trigger.range_break import RangeBreak


class F4SqueezeBreak(FamilyStrategyBase):
    family_name = "F4"

    def __init__(
        self,
        strategy_key: str,
        params: dict[str, Any],
        hours_utc: Sequence[int] | None = None,
        asset: str = "",
        timeframe: str = "M1",
    ) -> None:
        super().__init__(strategy_key, params, hours_utc, asset, timeframe)
        bb_len = int(params.get("bb_len", 20))
        bb_k = Decimal(str(params.get("bb_k", "2.0")))
        width_median_len = int(params.get("width_median_len", 20))
        self.width_ratio_max = Decimal(str(params.get("width_ratio_max", "0.8")))
        break_len = int(params.get("break_len", 20))
        volume_len = int(params.get("volume_len", 20))
        volume_min = Decimal(str(params.get("volume_min", "1.5")))

        self._regime = BBWidthRatio(length=bb_len, median_length=width_median_len, k=bb_k)
        self._trigger = RangeBreak(length=break_len)
        self._confirm = TickVolumeRatio(length=volume_len, minimum_ratio=volume_min)
        self._finalize_warmup()

    def _check_composition_gate(self, regime_out: Output | None) -> bool:
        if regime_out is None or regime_out.value is None:
            return False
        return regime_out.value <= self.width_ratio_max
