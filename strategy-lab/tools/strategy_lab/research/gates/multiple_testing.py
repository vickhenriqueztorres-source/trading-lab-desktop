"""Multiple testing correction: Benjamini-Hochberg (FDR) and permutation test (R-RES-7)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, getcontext

import numpy as np

getcontext().prec = 28


@dataclass(frozen=True)
class FDRResult:
    passed: bool
    p_value: Decimal
    critical_value: Decimal
    rank: int
    total_candidates: int
    alpha: Decimal


@dataclass(frozen=True)
class PermutationResult:
    passed: bool
    real_p_hat: Decimal
    percentile_99: Decimal
    num_permutations: int
    seed: int


def binomial_survival_p_value(wins: int, n: int, p_null: Decimal) -> Decimal:
    """Calculate one-sided binomial survival probability P(X >= wins | n, p_null) in Decimal."""
    if wins <= 0:
        return Decimal("1")
    if wins > n or n <= 0:
        return Decimal("0")
    if p_null <= Decimal("0"):
        return Decimal("0")
    if p_null >= Decimal("1"):
        return Decimal("1") if wins <= n else Decimal("0")

    # Sum P(X=j) from j=wins to n in Decimal
    comb_wins = Decimal(math.comb(n, wins))
    term = comb_wins * (p_null**wins) * ((Decimal("1") - p_null) ** (n - wins))
    total = term
    curr = term
    one_minus_p = Decimal("1") - p_null

    for j in range(wins, n):
        curr = curr * Decimal(n - j) / Decimal(j + 1) * p_null / one_minus_p
        total += curr

    return min(total, Decimal("1"))


def benjamini_hochberg(
    p_values: Sequence[Decimal],
    total_candidates: int,
    *,
    alpha: Decimal = Decimal("0.05"),
) -> list[bool]:
    """Apply Benjamini-Hochberg FDR control using total candidates N tested in the round."""
    if not p_values or total_candidates <= 0:
        return []

    indexed = sorted(enumerate(p_values), key=lambda item: item[1])
    d_total = Decimal(total_candidates)

    # Find largest k such that p_(k) <= (k / N) * alpha
    max_k = -1
    for rank_1based, (_, p_val) in enumerate(indexed, start=1):
        critical = (Decimal(rank_1based) / d_total) * alpha
        if p_val <= critical:
            max_k = rank_1based

    passed_flags = [False] * len(p_values)
    if max_k > 0:
        for rank_1based, (orig_idx, _) in enumerate(indexed, start=1):
            if rank_1based <= max_k:
                passed_flags[orig_idx] = True

    return passed_flags


def fdr_candidate_check(
    p_value: Decimal,
    rank: int,
    total_candidates: int,
    *,
    alpha: Decimal = Decimal("0.05"),
) -> FDRResult:
    """Check a single candidate's binomial p-value against its rank threshold."""
    if total_candidates <= 0 or rank <= 0:
        return FDRResult(
            passed=False,
            p_value=p_value,
            critical_value=Decimal("0"),
            rank=rank,
            total_candidates=total_candidates,
            alpha=alpha,
        )

    critical = (Decimal(rank) / Decimal(total_candidates)) * alpha
    passed = p_value <= critical
    return FDRResult(
        passed=passed,
        p_value=p_value,
        critical_value=critical,
        rank=rank,
        total_candidates=total_candidates,
        alpha=alpha,
    )


def permutation_test(
    trades_won: Sequence[bool],
    *,
    num_permutations: int = 1000,
    seed: int = 42,
    null_p: Decimal = Decimal("0.50"),
) -> PermutationResult:
    """Shuffle outcomes 1,000x under null hypothesis; require real p_hat > 99th percentile."""
    n = len(trades_won)
    if n == 0:
        return PermutationResult(
            passed=False,
            real_p_hat=Decimal("0"),
            percentile_99=Decimal("1"),
            num_permutations=num_permutations,
            seed=seed,
        )

    wins = sum(1 for won in trades_won if won)
    real_p_hat = Decimal(wins) / Decimal(n)

    rng = np.random.default_rng(seed)
    # Simulate null distribution of win counts under null_p
    simulated_wins = rng.binomial(n=n, p=float(null_p), size=num_permutations)
    simulated_p_hats = sorted(Decimal(int(w)) / Decimal(n) for w in simulated_wins)

    # 99th percentile index
    p99_idx = int(0.99 * num_permutations)
    if p99_idx >= num_permutations:
        p99_idx = num_permutations - 1
    p99 = simulated_p_hats[p99_idx]

    passed = real_p_hat > p99
    return PermutationResult(
        passed=passed,
        real_p_hat=real_p_hat,
        percentile_99=p99,
        num_permutations=num_permutations,
        seed=seed,
    )
