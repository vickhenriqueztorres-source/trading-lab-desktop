"""Central boundary proof for every registered local strategy primitive."""

from __future__ import annotations

from decimal import Decimal
from random import Random

import pytest

from apps.core.families.primitives import REGISTRY, Candle
from apps.core.families.primitives.base import Indicator


def _candle(index: int, random: Random) -> Candle:
    center = Decimal("100") + Decimal(random.randrange(11)) / Decimal("100")
    opening = center - Decimal("0.02") if index % 2 else center + Decimal("0.02")
    return Candle(
        ts=1_800_000_120 + index * 60,
        o=opening,
        h=max(opening, center) + Decimal("0.05"),
        l=min(opening, center) - Decimal("0.05"),
        c=center,
        tick_vol=100 + (index * 13) % 29,
    )


@pytest.mark.parametrize("indicator_type", REGISTRY.values(), ids=REGISTRY.keys())
def test_every_primitive_warmup_boundary(indicator_type: type[Indicator]) -> None:
    indicator = indicator_type()
    boundary = indicator.warmup_required
    random = Random(20260902)
    outputs = [indicator.update(_candle(index, random)) for index in range(boundary)]

    assert boundary >= 1
    assert all(output is None for output in outputs[:-1])
    assert outputs[-1] is not None
