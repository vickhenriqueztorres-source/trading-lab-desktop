"""Candidate scoring and financial metric calculations (R-RES-9)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, getcontext, localcontext

getcontext().prec = 28

MARGIN_SAFETY = Decimal("0.015")  # +1.5 pp
PAYOUT_STEP = Decimal("0.01")
MIN_GRID_PAYOUT = Decimal("0.01")
MAX_GRID_PAYOUT = Decimal("0.95")


@dataclass(frozen=True)
class CandidateScore:
    p_hat: Decimal
    wilson_lower: Decimal
    p_min: Decimal
    margin: Decimal
    ops_per_day: Decimal
    score: Decimal
    worst_streak: int
    result_1000_ops_stake10: Decimal
    payout_min: Decimal


def calculate_worst_streak(won_series: Sequence[bool]) -> int:
    """Calculate the maximum consecutive sequence of loss outcomes (won == False)."""
    max_streak = 0
    current_streak = 0
    for won in won_series:
        if not won:
            current_streak += 1
            if current_streak > max_streak:
                max_streak = current_streak
        else:
            current_streak = 0
    return max_streak


def calculate_result_1000_ops_stake10(p_hat: Decimal, payout_med: Decimal) -> Decimal:
    """R-RES-9: result_1000_ops_stake10 = 1000 * (p_hat * payout_med - (1 - p_hat)) * 10."""
    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_EVEN
        expected_ret_per_unit = (p_hat * payout_med) - (Decimal(1) - p_hat)
        total = Decimal(1000) * expected_ret_per_unit * Decimal(10)
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def calculate_payout_min(wilson_lower: Decimal) -> Decimal:
    """Smallest payout on grid 0.70..0.95 (step 0.01) with wilson_lower >= 1/(1+payout) + 0.015."""
    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_EVEN
        current = MIN_GRID_PAYOUT
        while current <= MAX_GRID_PAYOUT:
            threshold = (Decimal(1) / (Decimal(1) + current)) + MARGIN_SAFETY
            if wilson_lower >= threshold:
                return current
            current += PAYOUT_STEP
    # Fallback to MAX_GRID_PAYOUT if none strictly reached within normal bounds
    return MAX_GRID_PAYOUT


def score_candidate(
    p_hat: Decimal,
    wilson_lower: Decimal,
    p_min: Decimal,
    n: int,
    duration_days: Decimal,
    won_series: Sequence[bool],
    payout_med: Decimal = Decimal("0.85"),
) -> CandidateScore:
    """R-RES-9: compute margin, score, worst_streak, 1000-ops result and payout_min."""
    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_EVEN
        margin = wilson_lower - p_min
        ops_per_day = (
            (Decimal(n) / duration_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
            if duration_days > 0
            else Decimal(0)
        )
        score = (
            (margin * ops_per_day.sqrt()).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
            if ops_per_day > 0
            else Decimal(0)
        )
        worst_streak = calculate_worst_streak(won_series)
        result_1000 = calculate_result_1000_ops_stake10(p_hat, payout_med)
        payout_min = calculate_payout_min(wilson_lower)

    return CandidateScore(
        p_hat=p_hat,
        wilson_lower=wilson_lower,
        p_min=p_min,
        margin=margin,
        ops_per_day=ops_per_day,
        score=score,
        worst_streak=worst_streak,
        result_1000_ops_stake10=result_1000,
        payout_min=payout_min,
    )
