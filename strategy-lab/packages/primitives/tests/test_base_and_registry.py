"""R-PRIM-1..5: base validation, state contract, categories and parameter ranges."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from primitives import REGISTRY, VERSION, Candle, Category, by_category
from pydantic import ValidationError


def candle(index: int, close: str = "100") -> Candle:
    value = Decimal(close)
    return Candle(
        ts=1_800_000_000 + index * 60,
        o=value,
        h=value,
        l=value,
        c=value,
        tick_vol=10,
    )


def test_candle_strict_validation_and_bounds() -> None:
    assert candle(0).c == Decimal(100)
    with pytest.raises(ValidationError):
        Candle(ts=1, o=Decimal(1), h=Decimal(1), l=Decimal(1), c=Decimal(1), tick_vol=1)
    with pytest.raises(ValidationError):
        Candle(ts=1_800_000_000, o=Decimal(2), h=Decimal(1), l=Decimal(0), c=Decimal(1), tick_vol=1)
    with pytest.raises(ValidationError):
        Candle.model_validate(
            {"ts": 1_800_000_000, "o": "1", "h": "1", "l": "1", "c": "1", "tick_vol": 1}
        )


def test_registry_is_complete_typed_and_has_parameter_specs() -> None:
    assert VERSION == "1.0.0"
    assert (Path(__file__).parents[1] / "primitives" / "VERSION").read_text().strip() == VERSION
    assert len(REGISTRY) == 14
    assert len(by_category(Category.REGIME)) == 4
    assert len(by_category(Category.TRIGGER)) == 5
    assert len(by_category(Category.CONFIRM)) == 5
    for name, indicator_type in REGISTRY.items():
        assert name == indicator_type.name
        assert indicator_type.category in Category
        assert indicator_type.param_spec
        for parameter in indicator_type.param_spec.values():
            assert parameter.min <= parameter.max
            assert parameter.step > 0


@pytest.mark.parametrize("indicator_type", list(REGISTRY.values()), ids=list(REGISTRY))
def test_every_indicator_is_incremental_deterministic_and_resettable(indicator_type: type) -> None:
    indicator = indicator_type()
    stream = [candle(index, str(100 + index % 3)) for index in range(indicator.warmup_required + 3)]
    first = [indicator.update(item) for item in stream]
    indicator.reset()
    second = [indicator.update(item) for item in stream]
    assert first == second
    assert any(item is not None for item in first)
    indicator.reset()
    assert indicator.update(stream[0]) == first[0]
