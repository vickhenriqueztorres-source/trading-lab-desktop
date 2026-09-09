"""Combinatorially Symmetric Cross-Validation (CSCV) and PBO gate (R-RES-7)."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, getcontext

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


def _combinations(num_blocks: int = DEFAULT_NUM_BLOCKS) -> list[tuple[int, ...]]:
    """Return CSCV in-sample block combinations."""
    k = num_blocks // 2
    return list(itertools.combinations(range(num_blocks), k))


def compute_pbo_from_matrix(
    performance_matrix: Sequence[Sequence[Decimal | int | str]],
    num_blocks: int = DEFAULT_NUM_BLOCKS,
    *,
    max_threshold: Decimal = MAX_PBO_THRESHOLD,
) -> PBOResult:
    """Compute PBO via CSCV across 16 blocks for a matrix of shape (16, K)."""
    matrix = _decimal_matrix(performance_matrix)
    if len(matrix) != num_blocks:
        raise ValueError(f"Expected matrix of shape ({num_blocks}, K)")

    k_variants = len(matrix[0]) if matrix else 0
    if any(len(row) != k_variants for row in matrix):
        raise ValueError("Performance matrix rows must have the same width")
    if k_variants <= 1:
        matrix = [row + [Decimal("0")] for row in matrix]
        k_variants = 2

    combinations = _combinations(num_blocks)
    overfit_events = 0
    block_indexes = tuple(range(num_blocks))
    for in_sample_blocks in combinations:
        in_sample = set(in_sample_blocks)
        out_sample_blocks = tuple(index for index in block_indexes if index not in in_sample)
        in_returns = [
            sum((matrix[block][variant] for block in in_sample_blocks), Decimal("0"))
            for variant in range(k_variants)
        ]
        out_returns = [
            sum((matrix[block][variant] for block in out_sample_blocks), Decimal("0"))
            for variant in range(k_variants)
        ]
        best_variant = max(range(k_variants), key=lambda variant: in_returns[variant])
        best_oos = out_returns[best_variant]
        relative_rank = Decimal(sum(1 for value in out_returns if value <= best_oos)) / Decimal(
            k_variants
        )
        if relative_rank <= Decimal("0.5"):
            overfit_events += 1

    pbo_dec = (Decimal(overfit_events) / Decimal(len(combinations))).quantize(Decimal("0.000001"))

    passed = pbo_dec < max_threshold
    return PBOResult(
        passed=passed,
        pbo=pbo_dec,
        num_blocks=num_blocks,
        num_combinations=len(combinations),
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

    perf_matrix = [[Decimal("0") for _candidate in all_candidates] for _block in range(num_blocks)]

    for col_idx, cand in enumerate(all_candidates):
        log = replay_candidate(cand, ordered_candles, payout_lookup, registry=registry)
        for trade in log.trades:
            block_idx = min(int((trade.ts - min_ts) // block_duration), num_blocks - 1)
            perf_matrix[block_idx][col_idx] += trade.profit_ratio

    return compute_pbo_from_matrix(perf_matrix, num_blocks=num_blocks, max_threshold=max_threshold)


def _decimal_matrix(
    performance_matrix: Sequence[Sequence[Decimal | int | str]],
) -> list[list[Decimal]]:
    return [
        [item if isinstance(item, Decimal) else Decimal(str(item)) for item in row]
        for row in performance_matrix
    ]
