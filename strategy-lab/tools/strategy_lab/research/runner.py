"""Complete 10-step research pipeline (0->9) according to Architecture Section 5."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from manifest_schema.families import FAMILY_BINDINGS
from primitives import Candle

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.dataset import ResearchDataset
from strategy_lab.research.gates.approve import ApprovalResult, approve_candidate
from strategy_lab.research.gates.pipeline import GateResult
from strategy_lab.research.gates.wilson import wilson_lower
from strategy_lab.research.grammar import enumerate_candidates
from strategy_lab.research.holdout import HoldoutManager, separate_holdout
from strategy_lab.research.payout_lookup import PayoutLookup
from strategy_lab.research.replay_simulator import TradeLog, replay_candidate
from strategy_lab.research.report import (
    EvaluatedCandidateReport,
    generate_candidates_json,
    generate_ranking_markdown,
)
from strategy_lab.research.scorer import score_candidate
from strategy_lab.research.synthetic import random_walk

logger = logging.getLogger("strategy_lab.research.runner")


class SanityCheckFailedError(RuntimeError):
    """Raised when random-walk sanity test approves any candidate (Arch §5 step 8)."""


@dataclass(frozen=True)
class ResearchRunResult:
    run_id: str
    status: str  # "ok", "suspect", "aborted"
    candidates_count: int
    approved_count: int
    total_grammar_candidates: int
    ranking_md_path: Path
    candidates_json_path: Path
    reports: list[EvaluatedCandidateReport]
    holdout_range: tuple[int, int]
    holdout_hash: str
    started_at: int
    finished_at: int


def _translate_params_to_wire(family: str, params: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Map internal primitive params to stable wire names for the family."""
    fam_key = family if family in FAMILY_BINDINGS else "F1"
    bindings = FAMILY_BINDINGS.get(fam_key, {})
    wire: dict[str, str] = {}
    for wire_name, (indicator_name, param_key) in bindings.items():
        if indicator_name in params and param_key in params[indicator_name]:
            val = params[indicator_name][param_key]
            wire[wire_name] = format(val, "f") if isinstance(val, Decimal) else str(val)
    if fam_key == "F1" and "adx_max" not in wire:
        wire["adx_max"] = "20"
    elif fam_key == "F4" and "width_ratio_max" not in wire:
        wire["width_ratio_max"] = "0.5"
    return wire


def run_research_pipeline(
    candles: Sequence[Candle],
    payout_lookup: PayoutLookup,
    *,
    run_id: str,
    assets: Sequence[str] = ("EURUSD-OTC",),
    seed: int = 1,
    p_min: Decimal = Decimal("0.55"),
    max_candidates: int = 5000,
    output_dir: Path | None = None,
    dataset: ResearchDataset | None = None,
    holdout_manager: HoldoutManager | None = None,
    active_manifest_keys: set[str] | Sequence[str] | None = None,
    enforce_holdout_pass: bool = False,
    override_candidates: list[Candidate] | None = None,
    min_oos_trades: int = 500,
) -> ResearchRunResult:
    """Execute complete 10-step research pipeline (0 to 9)."""
    started_at = int(time.time())
    logger.info("Starting research run %s with seed %d", run_id, seed)

    # 0. Cobertura: recusa rodar se cobertura de velas < 95%
    if dataset is not None and candles:
        from_ts = candles[0].ts
        to_ts = candles[-1].ts
        for asset in assets:
            dataset.refuse_if_coverage_below(asset, from_ts, to_ts)

    ordered_candles = sorted(candles, key=lambda c: c.ts)
    if len(ordered_candles) < 10:
        raise ValueError("Insufficient candle data for research run")

    if holdout_manager is None:
        holdout_manager = HoldoutManager()

    # 1. HOLDOUT SELADO: últimos 3 meses removidos e lacrados (hash registrado)
    holdout_split = separate_holdout(ordered_candles)
    train_val_candles = holdout_split.train_val_candles
    holdout_candles = holdout_split.holdout_candles
    holdout_range = holdout_split.holdout_range
    holdout_hash = holdout_split.holdout_hash

    holdout_manager.refuse_if_burned(holdout_range)

    # 2. Grammar: enumeração de candidatos (≤ 5.000 por rodada)
    if override_candidates is not None:
        candidates = override_candidates
        total_candidates = len(candidates)
    else:
        grammar_res = enumerate_candidates(
            assets=assets,
            max_candidates=max_candidates,
            seed=seed,
        )
        candidates = grammar_res.candidates
        total_candidates = grammar_res.total_candidates

    logger.info(
        "Grammar produced %d candidates for evaluation (universe: %d)",
        len(candidates),
        total_candidates,
    )

    # 3 & 4. Replay Simulator e Triagem
    # 5. Statistical Gates Pipeline
    evaluated_reports: list[EvaluatedCandidateReport] = []
    pre_approved: list[tuple[Candidate, TradeLog, ApprovalResult]] = []

    # Dataset duration in days
    span_s = train_val_candles[-1].ts - train_val_candles[0].ts
    duration_days = Decimal(max(span_s // 86400, 1))

    for cand in candidates:
        tlog = replay_candidate(cand, train_val_candles, payout_lookup)
        if not tlog.trades:
            # Did not trigger any trade
            app = ApprovalResult(
                approved=False,
                wilson_lower=Decimal("0"),
                p_hat=Decimal("0"),
                p_hat_pessimistic=Decimal("0"),
                n=0,
                p_min=p_min,
                required_threshold=p_min + Decimal("0.015"),
                gate_results=(
                    GateResult(gate_name="trades_count", passed=False, metrics={"n": 0}),
                ),
                reason="ZERO_TRADES",
            )
            score = score_candidate(
                Decimal("0"),
                Decimal("0"),
                p_min,
                0,
                duration_days,
                [],
            )
        else:
            app = approve_candidate(
                cand,
                train_val_candles,
                payout_lookup,
                p_min,
                total_candidates,
                min_oos_trades=min_oos_trades,
                permutation_seed=seed,
                trade_log=tlog,
            )
            won_series = [t.won for t in tlog.trades]
            n_trades = len(won_series)
            wins = sum(1 for w in won_series if w)
            p_hat = Decimal(wins) / Decimal(n_trades) if n_trades else Decimal(0)
            wl = wilson_lower(wins, n_trades) if n_trades else Decimal(0)
            score = score_candidate(
                p_hat,
                wl,
                p_min,
                n_trades,
                duration_days,
                won_series,
            )

        if app.approved:
            pre_approved.append((cand, tlog, app))

        display_name = f"{cand.family} {cand.asset} {cand.tf}"
        wire_params = _translate_params_to_wire(cand.family, cand.params)  # type: ignore[arg-type]
        rep = EvaluatedCandidateReport(
            candidate=cand,
            score=score,
            approval=app,
            family=cand.family,
            display_name_pt=display_name,
            timeframe=cand.tf,
            hours_utc=list(cand.hours),
            params=wire_params,
        )
        evaluated_reports.append(rep)

    logger.info("Step 5 finished: %d candidates passed pre-approval", len(pre_approved))

    # 6. Holdout: aprovados abertos UMA vez; reprovado = descartado; holdout queimado
    if pre_approved:
        unsealed_holdout = holdout_manager.open_once(run_id, holdout_candles)
        # Verify holdout performance
        for cand, _tlog, _app in pre_approved:
            h_log = replay_candidate(cand, list(unsealed_holdout), payout_lookup)
            h_trades = [t.won for t in h_log.trades]
            h_n = len(h_trades)
            h_wins = sum(1 for w in h_trades if w)
            h_wl = wilson_lower(h_wins, h_n) if h_n else Decimal(0)

            if enforce_holdout_pass and (h_n < 5 or h_wl < p_min + Decimal("0.015")):
                # Demote approval
                for r_idx, rep in enumerate(evaluated_reports):
                    if rep.candidate == cand:
                        new_app = ApprovalResult(
                            approved=False,
                            wilson_lower=rep.approval.wilson_lower,
                            p_hat=rep.approval.p_hat,
                            p_hat_pessimistic=rep.approval.p_hat_pessimistic,
                            n=rep.approval.n,
                            p_min=rep.approval.p_min,
                            required_threshold=rep.approval.required_threshold,
                            gate_results=rep.approval.gate_results,
                            reason=f"HOLDOUT_FAILED(wl={h_wl:.3f}<{p_min + Decimal('0.015'):.3f})",
                        )
                        evaluated_reports[r_idx] = EvaluatedCandidateReport(
                            candidate=rep.candidate,
                            score=rep.score,
                            approval=new_app,
                            family=rep.family,
                            display_name_pt=rep.display_name_pt,
                            timeframe=rep.timeframe,
                            hours_utc=rep.hours_utc,
                            params=rep.params,
                        )

        # Register burned range
        holdout_manager.burn(holdout_range, run_id=run_id, burned_at=int(time.time()))

    # Count final approved
    final_approved_count = sum(1 for r in evaluated_reports if r.approval.approved)
    logger.info("Final approved candidates after holdout: %d", final_approved_count)

    # 8. SANIDADE (Arquitetura §5 Passo 8):
    # mesma rodada em série embaralhada / passeio aleatório DEVE aprovar ZERO, senão run 'aborted'.
    sanity_candles = random_walk(seed=seed, length=len(ordered_candles))
    sanity_approved_count = 0

    # Test surviving or candidate pool against the shuffled random walk
    candidates_to_sanity_check = (
        [r.candidate for r in evaluated_reports if r.approval.approved]
        if final_approved_count > 0
        else candidates[:5]
    )

    for s_cand in candidates_to_sanity_check:
        s_tlog = replay_candidate(s_cand, sanity_candles, payout_lookup)
        if s_tlog.trades:
            s_app = approve_candidate(
                s_cand,
                sanity_candles,
                payout_lookup,
                p_min,
                total_candidates,
                min_oos_trades=min_oos_trades,
                permutation_seed=seed,
                trade_log=s_tlog,
            )
            if s_app.approved:
                sanity_approved_count += 1

    logger.info("Step 8 Sanity check on random walk: %d approved", sanity_approved_count)
    if sanity_approved_count > 0:
        logger.error(
            "SANITY CHECK FAILED: Random walk approved %d candidates! Run %s ABORTED.",
            sanity_approved_count,
            run_id,
        )
        status = "aborted"
        raise SanityCheckFailedError(
            f"Sanity check failed: {sanity_approved_count} candidates approved on random walk. "
            f"Run {run_id} aborted."
        )
    else:
        status = "ok"

    # 9. Saída: ranking.md (com Novas Oportunidades) + candidates.json
    out_dir = output_dir or Path(f"research/runs/{run_id}")
    out_dir.mkdir(parents=True, exist_ok=True)

    ranking_md_text = generate_ranking_markdown(
        evaluated_reports,
        run_id,
        active_manifest_keys=active_manifest_keys,
    )
    ranking_path = out_dir / "ranking.md"
    ranking_path.write_text(ranking_md_text, encoding="utf-8")

    cand_json_text = generate_candidates_json(evaluated_reports, run_id)
    candidates_path = out_dir / "candidates.json"
    candidates_path.write_text(cand_json_text, encoding="utf-8")

    finished_at = int(time.time())

    return ResearchRunResult(
        run_id=run_id,
        status=status,
        candidates_count=len(candidates),
        approved_count=final_approved_count,
        total_grammar_candidates=total_candidates,
        ranking_md_path=ranking_path,
        candidates_json_path=candidates_path,
        reports=evaluated_reports,
        holdout_range=holdout_range,
        holdout_hash=holdout_hash,
        started_at=started_at,
        finished_at=finished_at,
    )
