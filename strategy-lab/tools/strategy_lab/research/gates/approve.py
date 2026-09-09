"""Formal candidate approval criteria and decision model (R-RES-8)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, getcontext

from primitives import Candle
from primitives.base import Indicator
from primitives.registry import REGISTRY

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.delay_penalty import apply_delay_penalty
from strategy_lab.research.gates.pipeline import GateResult, run_pipeline
from strategy_lab.research.gates.walk_forward import SIX_MONTHS_S, TWO_MONTHS_S
from strategy_lab.research.gates.wilson import wilson_lower_p
from strategy_lab.research.payout_lookup import PayoutLookup
from strategy_lab.research.replay_simulator import TradeLog, replay_candidate

getcontext().prec = 28

MIN_OUT_OF_SAMPLE_TRADES = 500  # n >= 500
REQUIRED_MARGIN = Decimal("0.015")  # +1.5 pp
PESSIMISTIC_DELAY_PENALTY = Decimal("0.010")  # -1.0 pp


@dataclass(frozen=True)
class ApprovalResult:
    approved: bool
    wilson_lower: Decimal
    p_hat: Decimal
    p_hat_pessimistic: Decimal
    n: int
    p_min: Decimal
    required_threshold: Decimal
    gate_results: tuple[GateResult, ...]
    reason: str = ""


def approve_candidate(
    candidate: Candidate,
    candles: Sequence[Candle],
    payout_lookup: PayoutLookup,
    p_min: Decimal,
    total_candidates: int,
    *,
    candidate_rank: int = 1,
    min_oos_trades: int = MIN_OUT_OF_SAMPLE_TRADES,
    required_margin: Decimal = REQUIRED_MARGIN,
    train_duration_s: int = SIX_MONTHS_S,
    test_duration_s: int = TWO_MONTHS_S,
    permutation_seed: int = 42,
    trade_log: TradeLog | None = None,
    registry: Mapping[str, type[Indicator]] = REGISTRY,
) -> ApprovalResult:
    """Approve candidate only if all 5 gates pass, n >= 500, and Wilson >= p_min + 1.5 pp."""
    ordered_candles = sorted(candles, key=lambda c: c.ts)
    if trade_log is None:
        trade_log = replay_candidate(candidate, ordered_candles, payout_lookup, registry=registry)
    n = len(trade_log.trades)
    p_hat = trade_log.p_hat
    p_pessimistic = apply_delay_penalty(p_hat, PESSIMISTIC_DELAY_PENALTY)
    w_lower = wilson_lower_p(p_pessimistic, n)
    required_threshold = p_min + required_margin
    payout_gate = GateResult(
        gate_name="payout_available",
        passed=trade_log.excluded_missing_payout == 0,
        metrics={"excluded_missing_payout": trade_log.excluded_missing_payout},
        reason="" if trade_log.excluded_missing_payout == 0 else "RES_PAYOUT_MISSING",
    )
    if not payout_gate.passed:
        return ApprovalResult(
            approved=False,
            wilson_lower=w_lower,
            p_hat=p_hat,
            p_hat_pessimistic=p_pessimistic,
            n=n,
            p_min=p_min,
            required_threshold=required_threshold,
            gate_results=(payout_gate,),
            reason=payout_gate.reason,
        )

    # 1. Sample size gate (n >= 500)
    sample_gate = GateResult(
        gate_name="sample_size",
        passed=n >= min_oos_trades,
        metrics={"n": n, "min_oos_trades": min_oos_trades},
        reason="" if n >= min_oos_trades else "INSUFFICIENT_OUT_OF_SAMPLE_TRADES",
    )
    if n < min_oos_trades:
        return ApprovalResult(
            approved=False,
            wilson_lower=w_lower,
            p_hat=p_hat,
            p_hat_pessimistic=p_pessimistic,
            n=n,
            p_min=p_min,
            required_threshold=required_threshold,
            gate_results=(payout_gate, sample_gate),
            reason="INSUFFICIENT_OUT_OF_SAMPLE_TRADES",
        )

    # 2. Pessimistic Wilson lower bound gate
    wilson_gate = GateResult(
        gate_name="pessimistic_wilson",
        passed=w_lower >= required_threshold,
        metrics={
            "p_hat": p_hat,
            "p_hat_pessimistic": p_pessimistic,
            "wilson_lower": w_lower,
            "required_threshold": required_threshold,
        },
        reason="" if w_lower >= required_threshold else "WILSON_LOWER_BELOW_THRESHOLD",
    )
    if w_lower < required_threshold:
        return ApprovalResult(
            approved=False,
            wilson_lower=w_lower,
            p_hat=p_hat,
            p_hat_pessimistic=p_pessimistic,
            n=n,
            p_min=p_min,
            required_threshold=required_threshold,
            gate_results=(payout_gate, sample_gate, wilson_gate),
            reason="WILSON_LOWER_BELOW_THRESHOLD",
        )

    # 3. Pipeline gates (Walk-Forward -> Stability -> FDR/Permutation -> Neighborhood -> PBO)
    gate_results = run_pipeline(
        candidate,
        ordered_candles,
        payout_lookup,
        p_min,
        total_candidates,
        candidate_rank=candidate_rank,
        train_duration_s=train_duration_s,
        test_duration_s=test_duration_s,
        permutation_seed=permutation_seed,
        trade_log=trade_log,
        registry=registry,
    )

    all_gates_passed = len(gate_results) == 5 and all(res.passed for res in gate_results)
    all_results = (payout_gate, sample_gate, wilson_gate, *gate_results)
    if not all_gates_passed:
        first_fail = next((r for r in gate_results if not r.passed), None)
        fail_reason = first_fail.reason if first_fail else "GATE_FAILED"
        return ApprovalResult(
            approved=False,
            wilson_lower=w_lower,
            p_hat=p_hat,
            p_hat_pessimistic=p_pessimistic,
            n=n,
            p_min=p_min,
            required_threshold=required_threshold,
            gate_results=all_results,
            reason=fail_reason,
        )

    return ApprovalResult(
        approved=True,
        wilson_lower=w_lower,
        p_hat=p_hat,
        p_hat_pessimistic=p_pessimistic,
        n=n,
        p_min=p_min,
        required_threshold=required_threshold,
        gate_results=all_results,
        reason="",
    )
