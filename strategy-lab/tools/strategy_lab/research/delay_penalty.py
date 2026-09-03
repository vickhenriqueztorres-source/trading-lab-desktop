"""Deterministic delay penalty for replay statistics (R-RES-6).

The chosen policy is direct subtraction from p_hat, not random relabeling. That keeps research
runs reproducible for the same dataset, candidate and seed.
"""

from __future__ import annotations

from decimal import Decimal


def apply_delay_penalty(p_hat: Decimal, penalty_pp: Decimal = Decimal("0.005")) -> Decimal:
    """Subtract a percentage-point penalty expressed as a probability fraction."""
    result = p_hat - penalty_pp
    return result if result > Decimal("0") else Decimal("0")
