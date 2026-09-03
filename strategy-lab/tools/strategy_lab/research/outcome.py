"""End-of-candle one-step settlement (R-RES-4)."""

from __future__ import annotations

from primitives import Candle
from primitives.base import Direction


def settle(direction: Direction, c_t: Candle, c_t1: Candle) -> bool:
    """Settle a one-candle trade; ties and `none` are losses by contract."""
    if direction == "call":
        return c_t1.c > c_t.c
    if direction == "put":
        return c_t1.c < c_t.c
    return False
