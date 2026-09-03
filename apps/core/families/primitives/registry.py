"""Registry of all 14 canonical primitives in the bot."""

from __future__ import annotations

from apps.core.families.primitives.base import Indicator
from apps.core.families.primitives.confirm.candle_rejection import CandleRejection
from apps.core.families.primitives.confirm.rsi_divergence import RSIDivergence
from apps.core.families.primitives.confirm.rsi_extreme import RSIExtreme
from apps.core.families.primitives.confirm.stoch_cross import StochCross
from apps.core.families.primitives.confirm.tick_volume_ratio import TickVolumeRatio
from apps.core.families.primitives.regime.adx import ADX
from apps.core.families.primitives.regime.bb_width_ratio import BBWidthRatio
from apps.core.families.primitives.regime.ema_alignment import EMAAlignment
from apps.core.families.primitives.regime.session_window import SessionWindow
from apps.core.families.primitives.trigger.bb_close_outside import BBCloseOutside
from apps.core.families.primitives.trigger.ema_pullback import EMAPullback
from apps.core.families.primitives.trigger.level_touch import LevelTouch
from apps.core.families.primitives.trigger.quadrant_majority import QuadrantMajority
from apps.core.families.primitives.trigger.range_break import RangeBreak

REGISTRY: dict[str, type[Indicator]] = {
    "adx": ADX,
    "bb_width_ratio": BBWidthRatio,
    "ema_alignment": EMAAlignment,
    "session_window": SessionWindow,
    "bb_close_outside": BBCloseOutside,
    "ema_pullback": EMAPullback,
    "level_touch": LevelTouch,
    "quadrant_majority": QuadrantMajority,
    "range_break": RangeBreak,
    "candle_rejection": CandleRejection,
    "rsi_extreme": RSIExtreme,
    "rsi_divergence": RSIDivergence,
    "stoch_cross": StochCross,
    "tick_volume_ratio": TickVolumeRatio,
}
