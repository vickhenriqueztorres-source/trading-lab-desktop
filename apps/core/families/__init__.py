"""Strategy families implementation for Trading Lab bot (R-BOT-5)."""

from __future__ import annotations

from apps.core.families.base import (
    FamilyStrategyBase,
    agreed_direction,
    is_within_trading_hours,
)
from apps.core.families.f1 import F1Reversal
from apps.core.families.f2 import F2Pullback
from apps.core.families.f3 import F3LevelRejection
from apps.core.families.f4 import F4SqueezeBreak
from apps.core.families.f5 import F5Quadrant

FAMILY_CLASSES: dict[str, type[FamilyStrategyBase]] = {
    "F1": F1Reversal,
    "F2": F2Pullback,
    "F3": F3LevelRejection,
    "F4": F4SqueezeBreak,
    "F5": F5Quadrant,
}

__all__ = [
    "FAMILY_CLASSES",
    "F1Reversal",
    "F2Pullback",
    "F3LevelRejection",
    "F4SqueezeBreak",
    "F5Quadrant",
    "FamilyStrategyBase",
    "agreed_direction",
    "is_within_trading_hours",
]
