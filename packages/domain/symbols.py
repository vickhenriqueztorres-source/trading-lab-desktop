from __future__ import annotations


def canonicalize_symbol(symbol: str) -> str:
    """Normalize equivalent broker spellings to one exposure key."""

    clean = symbol.strip().upper()
    if clean.startswith("FRX"):
        return clean[3:]
    if clean.startswith("OTC_"):
        return clean[4:]
    return clean
