"""Combinatorially Symmetric Cross-Validation (CSCV) and PBO gate (R-RES-7)."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, getcontext
from functools import lru_cache

import numpy as np
from primitives import Candle
from primitives.base import Indicator
from primitives.registry import REGISTRY

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.gates.neighborhood import generate_neighbors
from strategy_lab.research.payout_lookup import PayoutLookup
from strategy_lab.research.replay_simulator import replay_candidate

getcontext().prec = 28

MAX_PBO_THRESHOLD = Decimal("0.20")  # PBO < 20%
DEFAULT_NUM_BLOCKS = 16


@dataclass(frozen=True)
class PBOResult:
    passed: bool
    pbo: Decimal
    num_blocks: int
    num_combinations: int
    num_variants: int
    max_threshold: Decimal = MAX_PBO_THRESHOLD
    reason: str = ""


@lru_cache(maxsize=4)
def _combination_matrix(num_blocks: int = DEFAULT_NUM_BLOCKS) -> np.ndarray:
    """Precompute binary combination matrix C of shape (comb(S, S/2), S)."""
    k = num_blocks // 2
    combs = list(itertools.combinations(range(num_blocks), k))
    c_mat = np.zeros((len(combs), num_blocks), dtype=np.float64)
    for i, comb in enumerate(combs):
        c_mat[i, comb] = 1.0
    return c_mat


def compute_pbo_from_matrix(
    performance_matrix: np.ndarray,
    num_blocks: int = DEFAULT_NUM_BLOCKS,
    *,
    max_threshold: Decimal = MAX_PBO_THRESHOLD,
) -> PBOResult:
    """Compute PBO via CSCV across 16 blocks for a matrix of shape (16, K)."""
    if performance_matrix.ndim != 2 or performance_matrix.shape[0] != num_blocks:
        raise ValueError(f"Expected matrix of shape ({num_blocks}, K)")

    k_variants = performance_matrix.shape[1]
    if k_variants <= 1:
        # With single variant, compare against zero / break-even column
        zero_col = np.zeros((num_blocks, 1), dtype=np.float64)
        performance_matrix = np.hstack([performance_matrix, zero_col])
        k_variants = 2

    c_mat = _combination_matrix(num_blocks)
    num_combs = c_mat.shape[0]

    # In-sample and out-of-sample total returns for each combination
    r_is = c_mat @ performance_matrix  # (num_combs, K)
    r_oos = (1.0 - c_mat) @ performance_matrix  # (num_combs, K)

    best_is = np.argmax(r_is, axis=1)  # Best strategy index in-sample
    best_oos_vals = r_oos[np.arange(num_combs), best_is, None]  # (num_combs, 1)

    # Relative rank in OOS: fraction of strategies with return <= best_is OOS return
    oos_ranks = np.mean(r_oos <= best_oos_vals, axis=1)

    # Overfitting event: IS-optimal strategy falls in the bottom half OOS (relative rank <= 0.5)
    pbo_val = float(np.mean(oos_ranks <= 0.5))
    pbo_dec = Decimal(str(round(pbo_val, 6)))

    passed = pbo_dec < max_threshold
    return PBOResult(
        passed=passed,
        pbo=pbo_dec,
        num_blocks=num_blocks,
        num_combinations=num_combs,
        num_variants=k_variants,
        max_threshold=max_threshold,
        reason="" if passed else "PBO_EXCEEDS_20_PCT",
    )


def evaluate_pbo(
    candidate: Candidate,
    candles: Sequence[Candle],
    payout_lookup: PayoutLookup,
    *,
    neighbors: Sequence[Candidate] | None = None,
    num_blocks: int = DEFAULT_NUM_BLOCKS,
    max_threshold: Decimal = MAX_PBO_THRESHOLD,
    registry: Mapping[str, type[Indicator]] = REGISTRY,
) -> PBOResult:
    """Evaluate candidate PBO across 16 temporal blocks against parameter variations."""
    if neighbors is None:
        neighbors = generate_neighbors(candidate, registry=registry)

    all_candidates = [candidate] + list(neighbors)
    ordered_candles = sorted(candles, key=lambda c: c.ts)

    if not ordered_candles:
        return PBOResult(
            passed=False,
            pbo=Decimal("1"),
            num_blocks=num_blocks,
            num_combinations=0,
            num_variants=len(all_candidates),
            reason="NO_CANDLES",
        )

    min_ts = ordered_candles[0].ts
    max_ts = ordered_candles[-1].ts
    span = max_ts - min_ts + 1
    block_duration = max(span // num_blocks, 60)

    perf_matrix = np.zeros((num_blocks, len(all_candidates)), dtype=np.float64)

    for col_idx, cand in enumerate(all_candidates):
        log = replay_candidate(cand, ordered_candles, payout_lookup, registry=registry)
        for trade in log.trades:
            block_idx = min(int((trade.ts - min_ts) // block_duration), num_blocks - 1)
            perf_matrix[block_idx, col_idx] += float(trade.profit_ratio)

    return compute_pbo_from_matrix(perf_matrix, num_blocks=num_blocks, max_threshold=max_threshold)
