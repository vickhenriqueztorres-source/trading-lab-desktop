"""Sequential statistical gates pipeline with fail-closed short-circuiting (R-RES-7)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, getcontext

from primitives import Candle
from primitives.base import Indicator
from primitives.registry import REGISTRY

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.gates.multiple_testing import (
    binomial_survival_p_value,
    fdr_candidate_check,
    permutation_test,
)
from strategy_lab.research.gates.neighborhood import evaluate_neighborhood
from strategy_lab.research.gates.pbo import evaluate_pbo
from strategy_lab.research.gates.walk_forward import (
    SIX_MONTHS_S,
    TWO_MONTHS_S,
    evaluate_stability,
    generate_anchored_slices,
    partition_trades_into_windows,
)
from strategy_lab.research.payout_lookup import PayoutLookup
from strategy_lab.research.replay_simulator import TradeLog, replay_candidate

getcontext().prec = 28


@dataclass(frozen=True)
class GateResult:
    gate_name: str
    passed: bool
    metrics: Mapping[str, Decimal | str | int | float | bool]
    reason: str = ""


def run_pipeline(
    candidate: Candidate,
    candles: Sequence[Candle],
    payout_lookup: PayoutLookup,
    p_min: Decimal,
    total_candidates: int,
    *,
    candidate_rank: int = 1,
    train_duration_s: int = SIX_MONTHS_S,
    test_duration_s: int = TWO_MONTHS_S,
    permutation_seed: int = 42,
    trade_log: TradeLog | None = None,
    registry: Mapping[str, type[Indicator]] = REGISTRY,
) -> list[GateResult]:
    """Execute gates in fixed order:
    walk-forward -> stability -> FDR+permutation -> neighborhood -> PBO.

    Short-circuits immediately on first gate failure.
    """
    ordered_candles = sorted(candles, key=lambda c: c.ts)
    if not ordered_candles:
        return [
            GateResult(
                gate_name="walk_forward",
                passed=False,
                metrics={"candles_count": 0},
                reason="NO_CANDLES",
            )
        ]

    results: list[GateResult] = []
    if trade_log is None:
        trade_log = replay_candidate(candidate, ordered_candles, payout_lookup, registry=registry)

    # 1. Walk-Forward gate
    from_ts = ordered_candles[0].ts
    to_ts = ordered_candles[-1].ts
    slices = generate_anchored_slices(
        from_ts, to_ts, train_duration_s=train_duration_s, test_duration_s=test_duration_s
    )

    if not slices:
        # If dataset duration is shorter than standard slices, partition the available span
        span = to_ts - from_ts + 1
        if span >= 240:
            part_train = max(int(span * 0.6), 60)
            part_test = max(int(span * 0.2), 60)
            slices = generate_anchored_slices(
                from_ts, to_ts, train_duration_s=part_train, test_duration_s=part_test
            )

    windows = partition_trades_into_windows(trade_log.trades, slices) if slices else []
    wf_passed = len(windows) >= 2 and any(w.total > 0 for w in windows)
    window_p_hats = [w.p_hat for w in windows]

    wf_metrics: dict[str, Decimal | str | int | float | bool] = {
        "num_windows": len(windows),
        "window_p_hats": ",".join(str(p) for p in window_p_hats),
    }
    results.append(
        GateResult(
            gate_name="walk_forward",
            passed=wf_passed,
            metrics=wf_metrics,
            reason="" if wf_passed else "INSUFFICIENT_WALK_FORWARD_WINDOWS",
        )
    )
    if not wf_passed:
        return results

    # 2. Stability gate (no window < p_min and std < 3 pp)
    stability = evaluate_stability(window_p_hats, p_min=p_min)
    stab_metrics: dict[str, Decimal | str | int | float | bool] = {
        "mean_p_hat": stability.mean_p_hat,
        "std_p_hat": stability.std_p_hat,
        "min_p_hat": stability.min_p_hat,
        "p_min": p_min,
    }
    results.append(
        GateResult(
            gate_name="stability",
            passed=stability.passed,
            metrics=stab_metrics,
            reason=stability.reason,
        )
    )
    if not stability.passed:
        return results

    # 3. Multiple Testing gate (FDR 5% + Permutation 1,000x)
    total_trades = len(trade_log.trades)
    total_wins = trade_log.wins
    p_val = binomial_survival_p_value(total_wins, total_trades, p_null=p_min)
    fdr = fdr_candidate_check(p_val, rank=candidate_rank, total_candidates=total_candidates)

    perm = permutation_test(
        [t.won for t in trade_log.trades],
        num_permutations=1000,
        seed=permutation_seed,
        null_p=p_min,
    )
    mult_passed = fdr.passed and perm.passed
    mult_reason = ""
    if not fdr.passed:
        mult_reason = "FDR_CONTROL_FAILED"
    elif not perm.passed:
        mult_reason = "PERMUTATION_PERCENTILE_FAILED"

    mult_metrics: dict[str, Decimal | str | int | float | bool] = {
        "binomial_p_value": p_val,
        "fdr_critical": fdr.critical_value,
        "fdr_passed": fdr.passed,
        "rank": candidate_rank,
        "total_candidates": total_candidates,
        "real_p_hat": perm.real_p_hat,
        "percentile_99": perm.percentile_99,
        "perm_passed": perm.passed,
    }
    results.append(
        GateResult(
            gate_name="multiple_testing",
            passed=mult_passed,
            metrics=mult_metrics,
            reason=mult_reason,
        )
    )
    if not mult_passed:
        return results

    # 4. Neighborhood gate (mediana da vizinhança >= p_min + 1.5 pp)
    neigh = evaluate_neighborhood(
        candidate, ordered_candles, payout_lookup, p_min=p_min, registry=registry
    )
    neigh_metrics: dict[str, Decimal | str | int | float | bool] = {
        "median_p_hat": neigh.median_p_hat,
        "required_threshold": neigh.required_threshold,
        "num_neighbors": len(neigh.neighbor_p_hats),
    }
    results.append(
        GateResult(
            gate_name="neighborhood",
            passed=neigh.passed,
            metrics=neigh_metrics,
            reason=neigh.reason,
        )
    )
    if not neigh.passed:
        return results

    # 5. PBO gate (CSCV 16 blocks, PBO < 20%)
    pbo_res = evaluate_pbo(candidate, ordered_candles, payout_lookup, registry=registry)
    pbo_metrics: dict[str, Decimal | str | int | float | bool] = {
        "pbo": pbo_res.pbo,
        "max_threshold": pbo_res.max_threshold,
        "num_combinations": pbo_res.num_combinations,
    }
    results.append(
        GateResult(
            gate_name="pbo",
            passed=pbo_res.passed,
            metrics=pbo_metrics,
            reason=pbo_res.reason,
        )
    )

    return results
