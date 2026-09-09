"""Complete 10-step research pipeline (0->9) according to Architecture Section 5."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from manifest_schema.families import FAMILY_BINDINGS
from primitives import Candle

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.dataset import (
    SUPPORTED_TIMEFRAMES,
    DatasetOrigin,
    DatasetSnapshot,
    ResearchDataset,
)
from strategy_lab.research.evidence import (
    ArtifactStore,
    DatasetSnapshotRecord,
    EvidenceArtifact,
    EvidenceRepository,
    HoldoutReservation,
    ResearchAttemptRecord,
    ensure_durable_evidence_available,
    refuse_if_persistent_holdout_burned,
    trade_log_artifact_payload,
)
from strategy_lab.research.gates.approve import ApprovalResult, approve_candidate
from strategy_lab.research.gates.multiple_testing import binomial_survival_p_value
from strategy_lab.research.gates.pipeline import GateResult
from strategy_lab.research.gates.wilson import wilson_lower
from strategy_lab.research.grammar import DEFAULT_TRIAL_BUDGET, enumerate_candidates
from strategy_lab.research.holdout import THREE_MONTHS_S, HoldoutManager, separate_holdout
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
    dataset_fingerprint: str
    dataset_origin: str
    production_eligible: bool
    grammar_audit: dict[str, object]


def _translate_params_to_wire(family: str, params: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Map internal primitive params to stable wire names for the family."""
    if family not in FAMILY_BINDINGS:
        raise ValueError("RES_UNKNOWN_FAMILY")
    bindings = FAMILY_BINDINGS[family]
    wire: dict[str, str] = {}
    for wire_name, (indicator_name, param_key) in bindings.items():
        if indicator_name in params and param_key in params[indicator_name]:
            val = params[indicator_name][param_key]
            wire[wire_name] = format(val, "f") if isinstance(val, Decimal) else str(val)
    if family == "F1" and "adx_max" not in wire:
        wire["adx_max"] = "20"
    elif family == "F4" and "width_ratio_max" not in wire:
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
    max_candidates: int = DEFAULT_TRIAL_BUDGET,
    output_dir: Path | None = None,
    dataset: ResearchDataset | None = None,
    dataset_snapshot: DatasetSnapshot | None = None,
    holdout_manager: HoldoutManager | None = None,
    evidence_repository: EvidenceRepository | None = None,
    artifact_store: ArtifactStore | None = None,
    require_durable_evidence: bool | None = None,
    active_manifest_keys: set[str] | Sequence[str] | None = None,
    enforce_holdout_pass: bool = False,
    override_candidates: list[Candidate] | None = None,
    min_oos_trades: int = 500,
) -> ResearchRunResult:
    """Execute complete 10-step research pipeline (0 to 9)."""
    if dataset_snapshot is None:
        raise ValueError("RES_DATASET_SNAPSHOT_REQUIRED")
    if set(assets) != {dataset_snapshot.identity.asset}:
        raise ValueError("RES_DATASET_ASSET_MISMATCH")
    timeframe_name = SUPPORTED_TIMEFRAMES[dataset_snapshot.identity.timeframe_s]
    production_eligible = (
        dataset_snapshot.identity.origin in {DatasetOrigin.SUPABASE, DatasetOrigin.PARQUET}
        and dataset_snapshot.quality.approval_eligible
    )
    ensure_durable_evidence_available(
        production_eligible=(
            production_eligible if require_durable_evidence is None else require_durable_evidence
        ),
        evidence_repository=evidence_repository,
    )
    if evidence_repository is not None:
        evidence_repository.record_dataset_snapshot(
            DatasetSnapshotRecord.from_snapshot(dataset_snapshot)
        )
    started_at = _utc_now_ts()
    logger.info("Starting research run %s with seed %d", run_id, seed)

    # 0. Cobertura: recusa rodar se cobertura de velas < 95%
    if dataset is not None and candles:
        from_ts = candles[0].ts
        to_ts = candles[-1].ts
        for asset in assets:
            dataset.refuse_if_coverage_below(
                asset,
                from_ts,
                to_ts,
                timeframe_s=dataset_snapshot.identity.timeframe_s,
            )

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

    holdout_reservation: HoldoutReservation | None = None
    if evidence_repository is not None:
        holdout_reservation = HoldoutReservation.create(
            run_id=run_id,
            snapshot=dataset_snapshot,
            holdout_range=holdout_range,
            holdout_hash=holdout_hash,
        )
        refuse_if_persistent_holdout_burned(evidence_repository, holdout_reservation)
        evidence_repository.reserve_holdout(holdout_reservation)

    holdout_manager.refuse_if_burned(holdout_range)

    # 2. Grammar: enumeração de candidatos (≤ 5.000 por rodada)
    if override_candidates is not None:
        candidates = override_candidates
        total_candidates = len(candidates)
        grammar_audit: dict[str, object] = {
            "seed": seed,
            "trial_budget": len(candidates),
            "theoretical_candidates": len(candidates),
            "eligible_candidates": len(candidates),
            "sampled_candidates": len(candidates),
            "discarded_by_reason": {},
            "override_candidates": True,
        }
    else:
        grammar_res = enumerate_candidates(
            assets=assets,
            timeframes=[timeframe_name],
            max_candidates=max_candidates,
            seed=seed,
        )
        candidates = grammar_res.candidates
        total_candidates = grammar_res.total_candidates
        grammar_audit = grammar_res.audit_report()

    if any(
        candidate.asset != dataset_snapshot.identity.asset or candidate.tf != timeframe_name
        for candidate in candidates
    ):
        raise ValueError("RES_CANDIDATE_DATASET_MISMATCH")
    if any(candidate.family not in FAMILY_BINDINGS for candidate in candidates):
        raise ValueError("RES_UNKNOWN_FAMILY")

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
    trade_logs = _build_trade_logs(
        candidates,
        train_val_candles,
        payout_lookup,
        dataset_snapshot=dataset_snapshot,
    )
    candidate_ranks = _candidate_fdr_ranks(trade_logs, p_min)
    effective_enforce_holdout = enforce_holdout_pass or production_eligible

    for cand in candidates:
        tlog = trade_logs[cand]
        if tlog is None:
            app = ApprovalResult(
                approved=False,
                wilson_lower=Decimal("0"),
                p_hat=Decimal("0"),
                p_hat_pessimistic=Decimal("0"),
                n=0,
                p_min=p_min,
                required_threshold=p_min + Decimal("0.015"),
                gate_results=(
                    GateResult(
                        gate_name="dataset_volume",
                        passed=False,
                        metrics={"tick_volume_available": 0},
                        reason="RES_TICK_VOLUME_UNAVAILABLE",
                    ),
                ),
                reason="RES_TICK_VOLUME_UNAVAILABLE",
            )
            score = score_candidate(
                Decimal("0"),
                Decimal("0"),
                p_min,
                0,
                duration_days,
                [],
            )
        elif tlog.excluded_missing_payout > 0:
            app = ApprovalResult(
                approved=False,
                wilson_lower=Decimal("0"),
                p_hat=Decimal("0"),
                p_hat_pessimistic=Decimal("0"),
                n=len(tlog.trades),
                p_min=p_min,
                required_threshold=p_min + Decimal("0.015"),
                gate_results=(
                    GateResult(
                        gate_name="payout_available",
                        passed=False,
                        metrics={"excluded_missing_payout": tlog.excluded_missing_payout},
                        reason="RES_PAYOUT_MISSING",
                    ),
                ),
                reason="RES_PAYOUT_MISSING",
            )
            score = score_candidate(
                Decimal("0"),
                Decimal("0"),
                p_min,
                0,
                duration_days,
                [],
            )
        elif not tlog.trades:
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
                candidate_rank=candidate_ranks[cand],
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
                payout_med=_median_payout(tlog),
            )

        if app.approved and tlog is not None:
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
            holdout_passed=None,
        )
        evaluated_reports.append(rep)
        if evidence_repository is not None:
            _record_research_attempt(
                evidence_repository,
                artifact_store,
                run_id=run_id,
                candidate=cand,
                seed=seed,
                app=app,
                trade_log=tlog,
                dataset_fingerprint=dataset_snapshot.fingerprint,
            )

    logger.info("Step 5 finished: %d candidates passed pre-approval", len(pre_approved))

    # 6. Holdout: aprovados abertos UMA vez; reprovado = descartado; holdout queimado
    if pre_approved:
        if production_eligible and holdout_range[1] - holdout_range[0] < THREE_MONTHS_S:
            raise ValueError("RES_HOLDOUT_WINDOW_TOO_SHORT")
        if evidence_repository is not None and holdout_reservation is not None:
            evidence_repository.mark_holdout_opened(
                holdout_reservation.reservation_id,
                run_id=run_id,
                opened_at=_utc_now_ts(),
            )
        unsealed_holdout = holdout_manager.open_once(run_id, holdout_candles)
        # Verify holdout performance
        for cand, _tlog, _app in pre_approved:
            h_log = replay_candidate(cand, list(unsealed_holdout), payout_lookup)
            h_trades = [t.won for t in h_log.trades]
            h_n = len(h_trades)
            h_wins = sum(1 for w in h_trades if w)
            h_wl = wilson_lower(h_wins, h_n) if h_n else Decimal(0)
            h_passed = h_n >= min_oos_trades and h_wl >= p_min + Decimal("0.015")

            for r_idx, rep in enumerate(evaluated_reports):
                if rep.candidate == cand:
                    evaluated_reports[r_idx] = _with_holdout_passed(rep, h_passed)

            if effective_enforce_holdout and not h_passed:
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
                            holdout_passed=False,
                        )

        # Register burned range
        if evidence_repository is not None and holdout_reservation is not None:
            evidence_repository.burn_holdout(
                holdout_reservation.reservation_id,
                run_id=run_id,
                burned_at=_utc_now_ts(),
            )
        holdout_manager.burn(holdout_range, run_id=run_id, burned_at=_utc_now_ts())
    elif evidence_repository is not None and holdout_reservation is not None:
        evidence_repository.rollback_holdout(
            holdout_reservation.reservation_id,
            run_id=run_id,
            reason="no_pre_approved_candidates",
        )

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
        dataset_evidence=dataset_snapshot.public_evidence(),
        production_eligible=production_eligible,
    )
    ranking_path = out_dir / "ranking.md"
    ranking_path.write_text(ranking_md_text, encoding="utf-8")

    cand_json_text = generate_candidates_json(
        evaluated_reports,
        run_id,
        dataset_evidence=dataset_snapshot.public_evidence(),
        production_eligible=production_eligible,
    )
    candidates_path = out_dir / "candidates.json"
    candidates_path.write_text(cand_json_text, encoding="utf-8")

    finished_at = _utc_now_ts()

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
        dataset_fingerprint=dataset_snapshot.fingerprint,
        dataset_origin=dataset_snapshot.identity.origin.value,
        production_eligible=production_eligible,
        grammar_audit=grammar_audit,
    )


def _utc_now_ts() -> int:
    return int(datetime.now(tz=UTC).timestamp())


def _candidate_requires_tick_volume(candidate: Candidate) -> bool:
    components = {
        candidate.regime,
        candidate.trigger,
        candidate.confirm,
        *candidate.params.keys(),
    }
    return "tick_volume_ratio" in components


def _build_trade_logs(
    candidates: Sequence[Candidate],
    train_val_candles: list[Candle],
    payout_lookup: PayoutLookup,
    *,
    dataset_snapshot: DatasetSnapshot,
) -> dict[Candidate, TradeLog | None]:
    logs: dict[Candidate, TradeLog | None] = {}
    for candidate in candidates:
        if (
            _candidate_requires_tick_volume(candidate)
            and not dataset_snapshot.quality.tick_volume_available
        ):
            logs[candidate] = None
        else:
            logs[candidate] = replay_candidate(candidate, train_val_candles, payout_lookup)
    return logs


def _candidate_fdr_ranks(
    trade_logs: Mapping[Candidate, TradeLog | None],
    p_min: Decimal,
) -> dict[Candidate, int]:
    p_values: list[tuple[Candidate, Decimal]] = []
    for candidate, trade_log in trade_logs.items():
        if trade_log is None or not trade_log.trades or trade_log.excluded_missing_payout > 0:
            p_value = Decimal("1")
        else:
            p_value = binomial_survival_p_value(trade_log.wins, len(trade_log.trades), p_min)
        p_values.append((candidate, p_value))
    ordered = sorted(p_values, key=lambda item: (item[1], item[0].hash()))
    return {candidate: rank for rank, (candidate, _p_value) in enumerate(ordered, start=1)}


def _median_payout(trade_log: TradeLog) -> Decimal:
    payouts = sorted(trade.payout_return_ratio for trade in trade_log.trades)
    if not payouts:
        return Decimal("0")
    midpoint = len(payouts) // 2
    if len(payouts) % 2:
        return payouts[midpoint]
    return (payouts[midpoint - 1] + payouts[midpoint]) / Decimal("2")


def _with_holdout_passed(
    report: EvaluatedCandidateReport,
    holdout_passed: bool,
) -> EvaluatedCandidateReport:
    return EvaluatedCandidateReport(
        candidate=report.candidate,
        score=report.score,
        approval=report.approval,
        family=report.family,
        display_name_pt=report.display_name_pt,
        timeframe=report.timeframe,
        hours_utc=report.hours_utc,
        params=report.params,
        holdout_passed=holdout_passed,
    )


def _record_research_attempt(
    evidence_repository: EvidenceRepository,
    artifact_store: ArtifactStore | None,
    *,
    run_id: str,
    candidate: Candidate,
    seed: int,
    app: ApprovalResult,
    trade_log: TradeLog | None,
    dataset_fingerprint: str,
) -> None:
    wins = 0 if trade_log is None else trade_log.wins
    losses = 0 if trade_log is None else trade_log.losses
    counts = {
        "trades": 0 if trade_log is None else len(trade_log.trades),
        "wins": wins,
        "losses": losses,
        "excluded_missing_payout": 0 if trade_log is None else trade_log.excluded_missing_payout,
        "excluded_settlement_gap": 0 if trade_log is None else trade_log.excluded_settlement_gap,
    }
    evidence_repository.record_attempt(
        ResearchAttemptRecord.from_candidate(
            run_id=run_id,
            candidate=candidate,
            partition="train_val",
            seed=seed,
            status="pre_approved" if app.approved else "rejected",
            approval_state=app.reason if not app.approved else "PRE_APPROVED",
            counts=counts,
            dataset_fingerprint=dataset_fingerprint,
        )
    )
    if artifact_store is None or trade_log is None:
        return
    artifact = artifact_store.put_hash_addressed(
        kind="trade_log",
        payload=trade_log_artifact_payload(candidate, trade_log),
    )
    evidence_repository.record_artifact(
        EvidenceArtifact(
            sha256=artifact.sha256,
            kind=artifact.kind,
            storage_path=artifact.storage_path,
            byte_count=artifact.byte_count,
            run_id=run_id,
            candidate_hash=candidate.hash(),
        )
    )
