"""F1: Reversal family (ADX regime + BB close outside trigger + RSI extreme confirm)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from apps.core.families.base import FamilyStrategyBase
from apps.core.families.primitives.base import Output
from apps.core.families.primitives.confirm.rsi_extreme import RSIExtreme
from apps.core.families.primitives.regime.adx import ADX
from apps.core.families.primitives.trigger.bb_close_outside import BBCloseOutside


class F1Reversal(FamilyStrategyBase):
    family_name = "F1"

    def __init__(
        self,
        strategy_key: str,
        params: dict[str, Any],
        hours_utc: Sequence[int] | None = None,
        asset: str = "",
        timeframe: str = "M1",
    ) -> None:
        super().__init__(strategy_key, params, hours_utc, asset, timeframe)
        adx_len = int(params.get("adx_len", 14))
        self.adx_max = Decimal(str(params.get("adx_max", 25)))
        bb_len = int(params.get("bb_len", 20))
        bb_k = Decimal(str(params.get("bb_k", "2.0")))
        rsi_len = int(params.get("rsi_len", 14))
        rsi_lo = Decimal(str(params.get("rsi_lo", 30)))
        rsi_hi = Decimal(str(params.get("rsi_hi", 70)))

        self._regime = ADX(period=adx_len)
        self._trigger = BBCloseOutside(length=bb_len, k=bb_k)
        self._confirm = RSIExtreme(period=rsi_len, lower=rsi_lo, upper=rsi_hi)
        self._finalize_warmup()

    def _check_composition_gate(self, regime_out: Output | None) -> bool:
        if regime_out is None or regime_out.value is None:
            return False
        return regime_out.value <= self.adx_max
