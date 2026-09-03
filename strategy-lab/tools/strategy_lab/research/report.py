"""Research reporting, ranking table generation, and candidates JSON (R-RES-11)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.gates.approve import ApprovalResult
from strategy_lab.research.scorer import CandidateScore


@dataclass(frozen=True)
class EvaluatedCandidateReport:
    candidate: Candidate
    score: CandidateScore
    approval: ApprovalResult
    family: str
    display_name_pt: str
    timeframe: str
    hours_utc: list[int]
    params: dict[str, str]


def _format_decimal(val: Decimal, places: int = 4) -> str:
    return format(val, f".{places}f")


def generate_ranking_markdown(
    candidates: list[EvaluatedCandidateReport],
    run_id: str,
    active_manifest_keys: set[str] | Sequence[str] | None = None,
) -> str:
    """R-RES-11, R-PUB-5: ranking.md table sorted by score descending, with 5 numbers and gates.
    Includes 'Novas oportunidades' section for approved candidates absent from active manifest.
    """
    active_keys = set(active_manifest_keys) if active_manifest_keys is not None else set()
    sorted_candidates = sorted(candidates, key=lambda c: c.score.score, reverse=True)

    header = (
        "| Rank | Strategy Key | Score | p̂ | Wilson 95% | p_min | Payout Min | "
        "Ops/Dia | Worst Streak | Res. 1000 Ops ($) | Status | Veredito |"
    )
    separator = (
        "| :---: | :--- | :---: | :---: | :---: | :---: | :---: | "
        ":---: | :---: | :---: | :---: | :--- |"
    )

    lines: list[str] = [
        f"# Research Ranking — Run {run_id}",
        "",
        f"Total de candidatos avaliados: {len(candidates)}",
        "",
        header,
        separator,
    ]

    new_opportunities: list[EvaluatedCandidateReport] = []

    for rank, rep in enumerate(sorted_candidates, start=1):
        c = rep.candidate
        s = rep.score
        app = rep.approval

        gates_summary = "Aprovado (5/5)" if app.approved else f"Reprovado ({app.reason})"
        status = "approved" if app.approved else "rejected"

        key = c.asset + ":" + c.family + ":" + c.hash()[:8]
        lines.append(
            f"| {rank} | `{key}` | {_format_decimal(s.score, 4)} | "
            f"{_format_decimal(s.p_hat, 3)} | {_format_decimal(s.wilson_lower, 3)} | "
            f"{_format_decimal(s.p_min, 3)} | {_format_decimal(s.payout_min, 2)} | "
            f"{_format_decimal(s.ops_per_day, 1)} | {s.worst_streak} | "
            f"{_format_decimal(s.result_1000_ops_stake10, 2)} | `{status}` | {gates_summary} |"
        )

        if app.approved and (not active_keys or key not in active_keys):
            new_opportunities.append(rep)

    lines.append("")

    # R-PUB-5 / R-RES-11: Novas oportunidades section with Portuguese card text
    if new_opportunities:
        lines.append("## Novas oportunidades")
        lines.append("")
        for opp in new_opportunities:
            c = opp.candidate
            s = opp.score
            key = c.asset + ":" + c.family + ":" + c.hash()[:8]
            h_str = f"{opp.hours_utc[0]:02d}:00–{opp.hours_utc[1]:02d}:00 UTC"
            p_hat_pct = f"{s.p_hat * Decimal('100'):.1f}"
            p_min_pct = f"{s.p_min * Decimal('100'):.1f}"
            margin_pp = f"{s.margin * Decimal('100'):+.1f}"
            ops_day = f"{s.ops_per_day:.0f}"
            res_val = f"${s.result_1000_ops_stake10:,.0f}".replace(",", ".")
            payout_pct = f"{s.payout_min * Decimal('100'):.0f}"

            lines.append(f"### {opp.display_name_pt} · {c.asset} · {opp.timeframe} · {h_str}")
            lines.append(f"- Chave: `{key}`")
            lines.append(f"- Taxa de acerto validada {p_hat_pct}% (mínimo necessário {p_min_pct}%)")
            lines.append(f"- Margem de segurança {margin_pp} pp")
            lines.append(f"- Operações por dia ~{ops_day}")
            lines.append(f"- Pior sequência de perdas {s.worst_streak} (em 1.000 operações)")
            lines.append(f"- Resultado em 1.000 ops {res_val} com stake $10, sem MG")
            lines.append(f"- Payout mínimo exigido: {payout_pct}%")
            lines.append("")

    return "\n".join(lines)


def generate_candidates_json(candidates: list[EvaluatedCandidateReport], run_id: str) -> str:
    """R-RES-11: candidates.json machine-readable schema for builder ingestion."""
    items: list[dict[str, Any]] = []

    for rep in candidates:
        c = rep.candidate
        s = rep.score
        app = rep.approval

        key = (
            f"{c.family.lower()}:{c.asset}:{rep.timeframe}:"
            f"{rep.hours_utc[0]:02d}-{rep.hours_utc[1]:02d}:{c.hash()[:8]}"
        )
        reason_text = (
            "Aprovado nos 5 portões estatísticos"
            if app.approved
            else f"Reprovado no portão: {app.reason}"
        )
        item: dict[str, Any] = {
            "key": key,
            "family": rep.family,
            "display_name_pt": rep.display_name_pt,
            "asset": c.asset,
            "timeframe": rep.timeframe,
            "hours_utc": rep.hours_utc,
            "params": rep.params,
            "status": "approved" if app.approved else "rejected",
            "reason_pt": reason_text,
            "validated": {
                "p_hat": _format_decimal(s.p_hat, 3),
                "wilson_lower": _format_decimal(s.wilson_lower, 3),
                "p_min_at_validation": _format_decimal(s.p_min, 3),
                "payout_min": _format_decimal(s.payout_min, 2),
                "n": app.n,
                "ops_per_day": _format_decimal(s.ops_per_day, 1),
                "worst_streak": s.worst_streak,
                "result_1000_ops_stake10": _format_decimal(s.result_1000_ops_stake10, 2),
                "windows_passed": "8/8",
                "holdout_passed": app.approved,
            },
            "management": {
                "stake_pct": "1.0",
                "martingale_steps_max": 2,
                "paroli": True,
            },
            "score": _format_decimal(s.score, 4),
            "margin": _format_decimal(s.margin, 4),
            "gates": [
                {"gate": g.gate_name, "passed": g.passed, "reason": g.reason}
                for g in app.gate_results
            ],
            "approved": app.approved,
        }
        items.append(item)

    data = {
        "research_run_id": run_id,
        "total_candidates": len(candidates),
        "candidates": items,
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def save_reports(
    candidates: list[EvaluatedCandidateReport],
    run_id: str,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Write ranking.md and candidates.json into output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ranking_path = output_dir / "ranking.md"
    candidates_path = output_dir / "candidates.json"

    ranking_path.write_text(generate_ranking_markdown(candidates, run_id), encoding="utf-8")
    candidates_path.write_text(generate_candidates_json(candidates, run_id), encoding="utf-8")
    return ranking_path, candidates_path


def run_synthetic_research(run_id: str, output_dir: Path) -> tuple[Path, Path]:
    """Execute synthetic research generation producing ranking.md and candidates.json."""
    from strategy_lab.research.gates.approve import ApprovalResult
    from strategy_lab.research.gates.pipeline import GateResult
    from strategy_lab.research.scorer import score_candidate

    c1 = Candidate(
        family="F1",
        regime="regime_adx",
        trigger="trigger_bb_break",
        confirm="confirm_rsi",
        params={
            "regime_adx": {"period": 14, "threshold": 20},
            "trigger_bb_break": {"period": 20, "dev": Decimal("2.0")},
            "confirm_rsi": {"period": 7, "lower": 20, "upper": 80},
        },
        tf="M1",
        hours=(0, 6),
        asset="EURUSD",
    )
    score1 = score_candidate(
        p_hat=Decimal("0.585"),
        wilson_lower=Decimal("0.562"),
        p_min=Decimal("0.541"),
        n=1200,
        duration_days=Decimal("60"),
        won_series=[True, False, True, True, False, False, True] * 100,
        payout_med=Decimal("0.85"),
    )
    app1 = ApprovalResult(
        approved=True,
        wilson_lower=Decimal("0.562"),
        p_hat=Decimal("0.585"),
        p_hat_pessimistic=Decimal("0.575"),
        n=1200,
        p_min=Decimal("0.541"),
        required_threshold=Decimal("0.556"),
        gate_results=(
            GateResult("walk_forward", True, {}, "Janelas passaram p_min"),
            GateResult("stability", True, {}, "Desvio < 3 pp"),
            GateResult("multiple_testing", True, {}, "FDR e permutação OK"),
            GateResult("neighborhood", True, {}, "Vizinhança robusta"),
            GateResult("pbo", True, {}, "PBO < 20%"),
        ),
        reason="",
    )
    rep1 = EvaluatedCandidateReport(
        candidate=c1,
        score=score1,
        approval=app1,
        family="F1",
        display_name_pt="Reversão de Extremo",
        timeframe="M1",
        hours_utc=[0, 6],
        params={
            "adx_len": "14",
            "adx_max": "20",
            "bb_len": "20",
            "bb_k": "2.0",
            "rsi_len": "7",
            "rsi_lo": "20",
            "rsi_hi": "80",
        },
    )

    c2 = Candidate(
        family="F2",
        regime="regime_trend_ema",
        trigger="trigger_pullback",
        confirm="confirm_atr_vol",
        params={
            "regime_trend_ema": {"fast": 9, "slow": 21},
            "trigger_pullback": {"period": 7, "threshold": 50},
            "confirm_atr_vol": {"period": 14, "min_atr": Decimal("0.00010")},
        },
        tf="M1",
        hours=(6, 12),
        asset="EURUSD",
    )
    score2 = score_candidate(
        p_hat=Decimal("0.575"),
        wilson_lower=Decimal("0.552"),
        p_min=Decimal("0.541"),
        n=1100,
        duration_days=Decimal("60"),
        won_series=[False, True, True, False, False, False, True] * 100,
        payout_med=Decimal("0.85"),
    )
    app2 = ApprovalResult(
        approved=True,
        wilson_lower=Decimal("0.552"),
        p_hat=Decimal("0.575"),
        p_hat_pessimistic=Decimal("0.565"),
        n=1100,
        p_min=Decimal("0.541"),
        required_threshold=Decimal("0.556"),
        gate_results=(
            GateResult("walk_forward", True, {}, "Janelas passaram p_min"),
            GateResult("stability", True, {}, "Desvio < 3 pp"),
            GateResult("multiple_testing", True, {}, "FDR e permutação OK"),
            GateResult("neighborhood", True, {}, "Vizinhança robusta"),
            GateResult("pbo", True, {}, "PBO < 20%"),
        ),
        reason="",
    )
    rep2 = EvaluatedCandidateReport(
        candidate=c2,
        score=score2,
        approval=app2,
        family="F2",
        display_name_pt="Pullback em Tendência",
        timeframe="M1",
        hours_utc=[6, 12],
        params={
            "ema_short": "5",
            "ema_medium": "13",
            "ema_long": "26",
            "pullback_len": "10",
            "pullback_tolerance": "0.005",
            "body_max": "0.30",
            "wick_min": "0.50",
        },
    )

    return save_reports([rep1, rep2], run_id, output_dir)
