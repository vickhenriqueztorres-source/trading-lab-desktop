from __future__ import annotations

import pytest

from apps.ui.formatting import format_minor_units


def test_minor_units_formatting_never_requires_float() -> None:
    assert format_minor_units(123_456, "usd") == "USD 1,234.56"
    assert format_minor_units(-5, "USD") == "-USD 0.05"
    assert format_minor_units(4_500, "USD", positive_sign=True) == "+USD 45.00"


def test_minor_units_formatting_rejects_ambiguous_inputs() -> None:
    with pytest.raises(TypeError, match="integer"):
        format_minor_units(12.5, "USD")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="three-letter"):
        format_minor_units(100, "$")
