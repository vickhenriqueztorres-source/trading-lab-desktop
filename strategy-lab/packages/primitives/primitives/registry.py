"""Complete primitive registry grouped by the type-level indicator category."""

from __future__ import annotations

from primitives.base import Category, Indicator
from primitives.confirm import (
    CandleRejection,
    RSIDivergence,
    RSIExtreme,
    StochCross,
    TickVolumeRatio,
)
from primitives.regime import ADX, BBWidthRatio, EMAAlignment, SessionWindow
from primitives.trigger import BBCloseOutside, EMAPullback, LevelTouch, QuadrantMajority, RangeBreak

REGISTRY: dict[str, type[Indicator]] = {
    item.name: item
    for item in (
        ADX,
        BBWidthRatio,
        EMAAlignment,
        SessionWindow,
        BBCloseOutside,
        EMAPullback,
        LevelTouch,
        RangeBreak,
        QuadrantMajority,
        CandleRejection,
        RSIExtreme,
        StochCross,
        RSIDivergence,
        TickVolumeRatio,
    )
}


def by_category(category: Category) -> dict[str, type[Indicator]]:
    return {
        name: indicator for name, indicator in REGISTRY.items() if indicator.category is category
    }


__all__ = ["REGISTRY", "by_category"]
