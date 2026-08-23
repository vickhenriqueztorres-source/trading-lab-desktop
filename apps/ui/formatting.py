from __future__ import annotations


def format_minor_units(
    minor_units: int,
    currency: str,
    *,
    positive_sign: bool = False,
) -> str:
    """Format two-decimal minor units without converting money to float."""

    if type(minor_units) is not int:
        raise TypeError("minor units must be an integer")
    normalized_currency = currency.strip().upper()
    if len(normalized_currency) != 3 or not normalized_currency.isalpha():
        raise ValueError("currency must be a three-letter code")
    absolute = abs(minor_units)
    major, fractional = divmod(absolute, 100)
    sign = "-" if minor_units < 0 else "+" if positive_sign and minor_units > 0 else ""
    return f"{sign}{normalized_currency} {major:,}.{fractional:02d}"
