"""Wilson score interval lower bound in Decimal (R-RES-8)."""

from __future__ import annotations

from decimal import Decimal, getcontext

getcontext().prec = 28

DEFAULT_Z = Decimal("1.959964")  # 95% two-sided normal quantile


def wilson_lower(wins: int, n: int, z: Decimal = DEFAULT_Z) -> Decimal:
    """Calculate the Wilson lower bound given win count and total sample size."""
    if n <= 0 or wins <= 0:
        return Decimal("0")
    if wins >= n:
        d_n = Decimal(n)
        return Decimal("1") / (Decimal("1") + (z**2) / d_n)
    p_hat = Decimal(wins) / Decimal(n)
    return wilson_lower_p(p_hat, n, z=z)


def wilson_lower_p(p_hat: Decimal, n: int, z: Decimal = DEFAULT_Z) -> Decimal:
    """Calculate the Wilson lower bound given probability point estimate and sample size."""
    if n <= 0 or p_hat <= Decimal("0"):
        return Decimal("0")
    if p_hat >= Decimal("1"):
        d_n = Decimal(n)
        return Decimal("1") / (Decimal("1") + (z**2) / d_n)

    d_n = Decimal(n)
    z2 = z**2
    denominator = Decimal("1") + (z2 / d_n)
    center = p_hat + (z2 / (Decimal("2") * d_n))
    variance_term = (p_hat * (Decimal("1") - p_hat) / d_n) + (z2 / (Decimal("4") * (d_n**2)))
    spread = z * variance_term.sqrt()
    result = (center - spread) / denominator
    return result if result > Decimal("0") else Decimal("0")
