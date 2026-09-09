"""Offline portfolio selection by executable contribution and robustness (CAT-12).

The selector consumes only candidates that already passed the individual gates.
It works exclusively on a development partition, freezes its configuration and
selection, and exposes a separate one-shot holdout evaluator.  It never imports
the publisher, the desktop bot, or a broker connector.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal

from strategy_lab.research.gates.wilson import DEFAULT_Z, wilson_lower
from strategy_lab.research.portfolio_replay import (
    PortfolioOpportunity,
    PortfolioReplayConfig,
    PortfolioReplayResult,
    run_portfolio_replay,
)

SELECTION_METHOD_VERSION = "tl.portfolio-selection.v1"
DEFAULT_TARGET_SIZES = (10, 20, 30, 50, 100)
ZERO = Decimal("0")
ONE = Decimal("1")
HALF_PP = Decimal("0.005")
ONE_PP = Decimal("0.010")


class SelectionReason(StrEnum):
    INDIVIDUAL_GATE_FAILED = "INDIVIDUAL_GATE_FAILED"
    CORRELATION_LIMIT = "CORRELATION_LIMIT"
    ASSET_CONCENTRATION_LIMIT = "ASSET_CONCENTRATION_LIMIT"
    HOUR_CONCENTRATION_LIMIT = "HOUR_CONCENTRATION_LIMIT"
    NO_MARGINAL_EXECUTABLE_GAIN = "NO_MARGINAL_EXECUTABLE_GAIN"
    ATTEMPT_BUDGET_EXHAUSTED = "ATTEMPT_BUDGET_EXHAUSTED"
    PORTFOLIO_CAPACITY_REACHED = "PORTFOLIO_CAPACITY_REACHED"
    LOWER_MARGINAL_RANK = "LOWER_MARGINAL_RANK"


class HoldoutReason(StrEnum):
    PASSED = "PASSED"
    INSUFFICIENT_TRADES = "INSUFFICIENT_TRADES"
    WILSON_BELOW_THRESHOLD = "WILSON_BELOW_THRESHOLD"
    EV_BELOW_THRESHOLD = "EV_BELOW_THRESHOLD"


@dataclass(frozen=True, slots=True)
class DevelopmentRecipe:
    """Individually-gated recipe and its development-only opportunities."""

    recipe_key: str
    evidence_ref: str
    individually_approved: bool
    individual_reason: str
    robustness_score: Decimal
    opportunities: tuple[PortfolioOpportunity, ...]
    dataset_kind: Literal["real_market", "synthetic"] = "real_market"

    def __post_init__(self) -> None:
        if not self.recipe_key or not self.evidence_ref:
            raise ValueError("RES_PORTFOLIO_RECIPE_IDENTITY_REQUIRED")
        if not ZERO <= self.robustness_score <= ONE:
            raise ValueError("RES_PORTFOLIO_ROBUSTNESS_RANGE")
        if any(item.recipe_key != self.recipe_key for item in self.opportunities):
            raise ValueError("RES_PORTFOLIO_RECIPE_KEY_MISMATCH")


@dataclass(frozen=True, slots=True)
class PortfolioSelectionConfig:
    """Budgets and thresholds frozen before any portfolio holdout is opened."""

    replay: PortfolioReplayConfig
    target_sizes: tuple[int, ...] = DEFAULT_TARGET_SIZES
    max_recipes: int = 100
    max_pairwise_event_overlap: Decimal = Decimal("0.60")
    max_asset_concentration: Decimal = Decimal("0.75")
    max_hour_concentration: Decimal = Decimal("0.60")
    min_marginal_executed: int = 1
    attempted_portfolio_budget: int = 6_000
    min_holdout_trades: int = 100
    min_holdout_wilson_lower: Decimal = Decimal("0.50")
    min_holdout_ev_per_stake: Decimal = ZERO
    method_version: str = SELECTION_METHOD_VERSION

    def __post_init__(self) -> None:
        if not self.target_sizes or any(size <= 0 for size in self.target_sizes):
            raise ValueError("RES_PORTFOLIO_TARGET_SIZES_INVALID")
        if tuple(sorted(set(self.target_sizes))) != self.target_sizes:
            raise ValueError("RES_PORTFOLIO_TARGET_SIZES_NOT_CANONICAL")
        if self.max_recipes <= 0 or self.max_recipes > 100:
            raise ValueError("RES_PORTFOLIO_CAPACITY_INVALID")
        if self.min_marginal_executed <= 0 or self.attempted_portfolio_budget <= 0:
            raise ValueError("RES_PORTFOLIO_BUDGET_INVALID")
        if self.attempted_portfolio_budget <= len(self.target_sizes):
            raise ValueError("RES_PORTFOLIO_BUDGET_MUST_RESERVE_COMPARISONS")
        if self.min_holdout_trades <= 0:
            raise ValueError("RES_PORTFOLIO_HOLDOUT_SAMPLE_INVALID")
        for value in (
            self.max_pairwise_event_overlap,
            self.max_asset_concentration,
            self.max_hour_concentration,
            self.min_holdout_wilson_lower,
        ):
            if not ZERO <= value <= ONE:
                raise ValueError("RES_PORTFOLIO_THRESHOLD_RANGE")

    @property
    def config_hash(self) -> str:
        return _stable_hash(_config_payload(self))


@dataclass(frozen=True, slots=True)
class SelectionStep:
    position: int
    recipe_key: str
    marginal_executed: int
    robustness_score: Decimal
    max_event_overlap: Decimal
    executable_total: int


@dataclass(frozen=True, slots=True)
class PortfolioMetrics:
    sample_size: int
    wins: int
    p_hat: Decimal
    wilson_lower: Decimal
    wilson_upper: Decimal
    mean_payout_return_ratio: Decimal | None
    break_even_payout_return_ratio: Decimal | None
    estimated_ev_per_stake: Decimal | None
    sensitivity_ev_minus_half_pp: Decimal | None
    sensitivity_ev_minus_one_pp: Decimal | None
    executable_operations_per_day: Decimal
    maximum_drawdown: Decimal
    worst_loss_streak: int


@dataclass(frozen=True, slots=True)
class SizeComparison:
    requested_size: int
    actual_size: int
    recipe_keys: tuple[str, ...]
    replay_hash: str
    metrics: PortfolioMetrics


@dataclass(frozen=True, slots=True)
class FrozenPortfolioSelection:
    config: PortfolioSelectionConfig
    selected_keys: tuple[str, ...]
    evidence_refs: Mapping[str, str]
    steps: tuple[SelectionStep, ...]
    excluded: Mapping[str, SelectionReason]
    attempted_portfolios: int
    evaluated_opportunities: int
    size_comparisons: tuple[SizeComparison, ...]
    development_result: PortfolioReplayResult
    development_metrics: PortfolioMetrics
    dataset_kinds: tuple[str, ...]
    selection_hash: str


@dataclass(frozen=True, slots=True)
class HoldoutEvaluation:
    selection_hash: str
    holdout_snapshot_id: str
    passed: bool
    reason: HoldoutReason
    result: PortfolioReplayResult
    metrics: PortfolioMetrics


@dataclass(frozen=True, slots=True)
class PortfolioManifestDraft:
    """Evidence-linked hand-off for CAT-13; deliberately unsigned and non-publishable."""

    selection_hash: str
    config_hash: str
    holdout_snapshot_id: str
    status: Literal["holdout_passed", "rejected"]
    publish_automatically: Literal[False]
    signature: None
    entries: tuple[Mapping[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "config_hash": self.config_hash,
            "entries": [dict(entry) for entry in self.entries],
            "holdout_snapshot_id": self.holdout_snapshot_id,
            "publish_automatically": self.publish_automatically,
            "selection_hash": self.selection_hash,
            "signature": self.signature,
            "status": self.status,
        }


@dataclass(slots=True)
class HoldoutLedger:
    """Process-local fail-closed guard; durable range burning remains CAT-05's concern."""

    _opened_snapshot_ids: set[str] = field(default_factory=set)

    def claim(self, holdout_snapshot_id: str) -> None:
        if not holdout_snapshot_id:
            raise ValueError("RES_PORTFOLIO_HOLDOUT_SNAPSHOT_REQUIRED")
        if holdout_snapshot_id in self._opened_snapshot_ids:
            raise RuntimeError("RES_PORTFOLIO_HOLDOUT_ALREADY_OPENED")
        self._opened_snapshot_ids.add(holdout_snapshot_id)


@dataclass(frozen=True, slots=True)
class _Addition:
    recipe: DevelopmentRecipe
    result: PortfolioReplayResult
    marginal: int
    max_overlap: Decimal


def select_portfolio(
    recipes: Sequence[DevelopmentRecipe],
    config: PortfolioSelectionConfig,
) -> FrozenPortfolioSelection:
    """Select a deterministic development portfolio and freeze it before holdout."""
    unique: dict[str, DevelopmentRecipe] = {}
    for recipe in recipes:
        if recipe.recipe_key in unique:
            raise ValueError("RES_PORTFOLIO_DUPLICATE_RECIPE")
        unique[recipe.recipe_key] = recipe
    approved_dataset_kinds = {
        recipe.dataset_kind for recipe in unique.values() if recipe.individually_approved
    }
    if len(approved_dataset_kinds) > 1:
        raise ValueError("RES_PORTFOLIO_DATASET_KIND_MIXED")

    excluded: dict[str, SelectionReason] = {}
    eligible: dict[str, DevelopmentRecipe] = {}
    for key, recipe in sorted(unique.items()):
        if not recipe.individually_approved:
            excluded[key] = SelectionReason.INDIVIDUAL_GATE_FAILED
        else:
            eligible[key] = recipe

    selected: list[DevelopmentRecipe] = []
    steps: list[SelectionStep] = []
    attempts = 0
    evaluated_opportunities = 0
    selection_attempt_budget = config.attempted_portfolio_budget - len(config.target_sizes)
    current = run_portfolio_replay((), config.replay, compute_marginal=False)
    terminal_reasons: dict[str, SelectionReason] = {}

    while eligible and len(selected) < config.max_recipes:
        additions: list[_Addition] = []
        round_reasons: dict[str, SelectionReason] = {}
        for key, recipe in sorted(eligible.items()):
            if attempts >= selection_attempt_budget:
                round_reasons[key] = SelectionReason.ATTEMPT_BUDGET_EXHAUSTED
                continue
            overlap = _maximum_overlap(recipe, selected)
            if overlap > config.max_pairwise_event_overlap:
                round_reasons[key] = SelectionReason.CORRELATION_LIMIT
                continue
            attempts += 1
            proposed_opportunities = _opportunities((*selected, recipe))
            evaluated_opportunities += len(proposed_opportunities)
            proposed = run_portfolio_replay(
                proposed_opportunities,
                config.replay,
                compute_marginal=False,
            )
            marginal = proposed.executed_total - current.executed_total
            if marginal < config.min_marginal_executed:
                round_reasons[key] = SelectionReason.NO_MARGINAL_EXECUTABLE_GAIN
                continue
            if selected and _maximum_concentration(proposed.concentration_by_asset) > (
                config.max_asset_concentration
            ):
                round_reasons[key] = SelectionReason.ASSET_CONCENTRATION_LIMIT
                continue
            if selected and _hour_concentration(proposed) > config.max_hour_concentration:
                round_reasons[key] = SelectionReason.HOUR_CONCENTRATION_LIMIT
                continue
            additions.append(_Addition(recipe, proposed, marginal, overlap))

        if not additions:
            terminal_reasons.update(round_reasons)
            break
        additions.sort(
            key=lambda item: (
                -item.marginal,
                -item.recipe.robustness_score,
                item.max_overlap,
                item.recipe.recipe_key,
            )
        )
        chosen = additions[0]
        selected.append(chosen.recipe)
        current = chosen.result
        steps.append(
            SelectionStep(
                position=len(selected),
                recipe_key=chosen.recipe.recipe_key,
                marginal_executed=chosen.marginal,
                robustness_score=chosen.recipe.robustness_score,
                max_event_overlap=chosen.max_overlap,
                executable_total=current.executed_total,
            )
        )
        eligible.pop(chosen.recipe.recipe_key)
        for addition in additions[1:]:
            terminal_reasons[addition.recipe.recipe_key] = SelectionReason.LOWER_MARGINAL_RANK

    if eligible:
        capacity_reason = (
            SelectionReason.ATTEMPT_BUDGET_EXHAUSTED
            if attempts >= selection_attempt_budget
            else SelectionReason.PORTFOLIO_CAPACITY_REACHED
        )
        for key in eligible:
            excluded[key] = terminal_reasons.get(key, capacity_reason)
    for key, reason in terminal_reasons.items():
        if key not in {item.recipe_key for item in steps}:
            excluded[key] = reason

    size_comparisons: list[SizeComparison] = []
    for requested_size in config.target_sizes:
        prefix = tuple(selected[:requested_size])
        compared = run_portfolio_replay(
            _opportunities(prefix), config.replay, compute_marginal=False
        )
        attempts += 1
        evaluated_opportunities += sum(len(item.opportunities) for item in prefix)
        size_comparisons.append(
            SizeComparison(
                requested_size=requested_size,
                actual_size=len(prefix),
                recipe_keys=tuple(item.recipe_key for item in prefix),
                replay_hash=compared.reproducibility_hash,
                metrics=portfolio_metrics(compared),
            )
        )

    selected_keys = tuple(item.recipe_key for item in selected)
    evidence_refs = {item.recipe_key: item.evidence_ref for item in selected}
    dataset_kinds = tuple(sorted({item.dataset_kind for item in selected}))
    selection_hash = _stable_hash(
        {
            "config_hash": config.config_hash,
            "dataset_kinds": list(dataset_kinds),
            "evidence_refs": evidence_refs,
            "selected_keys": list(selected_keys),
        }
    )
    return FrozenPortfolioSelection(
        config=config,
        selected_keys=selected_keys,
        evidence_refs=evidence_refs,
        steps=tuple(steps),
        excluded=dict(sorted(excluded.items())),
        attempted_portfolios=attempts,
        evaluated_opportunities=evaluated_opportunities,
        size_comparisons=tuple(size_comparisons),
        development_result=current,
        development_metrics=portfolio_metrics(current),
        dataset_kinds=dataset_kinds,
        selection_hash=selection_hash,
    )


def evaluate_frozen_holdout(
    selection: FrozenPortfolioSelection,
    holdout_opportunities: Sequence[PortfolioOpportunity],
    *,
    holdout_snapshot_id: str,
    holdout_period_start_ts: int,
    holdout_period_end_ts: int,
    ledger: HoldoutLedger,
) -> HoldoutEvaluation:
    """Open a holdout exactly once and reject without any retuning on failure."""
    ledger.claim(holdout_snapshot_id)
    selected = set(selection.selected_keys)
    filtered = tuple(item for item in holdout_opportunities if item.recipe_key in selected)
    replay_config = replace(
        selection.config.replay,
        snapshot_id=holdout_snapshot_id,
        period_start_ts=holdout_period_start_ts,
        period_end_ts=holdout_period_end_ts,
    )
    result = run_portfolio_replay(filtered, replay_config, compute_marginal=False)
    metrics = portfolio_metrics(result)
    if metrics.sample_size < selection.config.min_holdout_trades:
        reason = HoldoutReason.INSUFFICIENT_TRADES
    elif metrics.wilson_lower < selection.config.min_holdout_wilson_lower:
        reason = HoldoutReason.WILSON_BELOW_THRESHOLD
    elif (
        metrics.estimated_ev_per_stake is None
        or metrics.estimated_ev_per_stake < selection.config.min_holdout_ev_per_stake
    ):
        reason = HoldoutReason.EV_BELOW_THRESHOLD
    else:
        reason = HoldoutReason.PASSED
    return HoldoutEvaluation(
        selection_hash=selection.selection_hash,
        holdout_snapshot_id=holdout_snapshot_id,
        passed=reason == HoldoutReason.PASSED,
        reason=reason,
        result=result,
        metrics=metrics,
    )


def build_manifest_draft(
    selection: FrozenPortfolioSelection,
    holdout: HoldoutEvaluation,
) -> PortfolioManifestDraft:
    """Create an evidence-only draft; CAT-13 must validate, sign and publish manually."""
    if holdout.selection_hash != selection.selection_hash:
        raise ValueError("RES_PORTFOLIO_HOLDOUT_SELECTION_MISMATCH")
    entries = tuple(
        {
            "evidence_ref": selection.evidence_refs[key],
            "recipe_key": key,
            "selection_hash": selection.selection_hash,
            "status": "approved" if holdout.passed else "rejected",
        }
        for key in selection.selected_keys
    )
    return PortfolioManifestDraft(
        selection_hash=selection.selection_hash,
        config_hash=selection.config.config_hash,
        holdout_snapshot_id=holdout.holdout_snapshot_id,
        status="holdout_passed" if holdout.passed else "rejected",
        publish_automatically=False,
        signature=None,
        entries=entries,
    )


def portfolio_metrics(result: PortfolioReplayResult) -> PortfolioMetrics:
    """Compute OOS quality/risk numbers without converting monetary values to float."""
    n = result.executed_total
    wins = sum(1 for trade in result.trades if trade.won)
    p_hat = Decimal(wins) / Decimal(n) if n else ZERO
    lower = wilson_lower(wins, n)
    upper = _wilson_upper(wins, n)
    mean_payout = (
        sum((trade.payout_return_ratio for trade in result.trades), start=ZERO) / Decimal(n)
        if n
        else None
    )
    fair_payout = ((ONE - p_hat) / p_hat) if p_hat > ZERO else None
    estimated_ev = _ev(p_hat, mean_payout)
    return PortfolioMetrics(
        sample_size=n,
        wins=wins,
        p_hat=p_hat,
        wilson_lower=lower,
        wilson_upper=upper,
        mean_payout_return_ratio=mean_payout,
        break_even_payout_return_ratio=fair_payout,
        estimated_ev_per_stake=estimated_ev,
        sensitivity_ev_minus_half_pp=_ev(max(ZERO, p_hat - HALF_PP), mean_payout),
        sensitivity_ev_minus_one_pp=_ev(max(ZERO, p_hat - ONE_PP), mean_payout),
        executable_operations_per_day=result.executable_operations_per_day,
        maximum_drawdown=_maximum_drawdown(result),
        worst_loss_streak=_worst_loss_streak(result),
    )


def selection_report_markdown(
    selection: FrozenPortfolioSelection,
    holdout: HoldoutEvaluation | None = None,
) -> str:
    """Render the frozen development report with explicit provenance and budgets."""
    metrics = selection.development_metrics
    lines = [
        "# CAT-12 — Seleção Offline de Portfólio",
        "",
        f"Método: `{selection.config.method_version}`",
        f"Snapshot de desenvolvimento: `{selection.config.replay.snapshot_id}`",
        f"Seed: `{selection.config.replay.seed}`",
        f"Config hash: `{selection.config.config_hash}`",
        f"Selection hash: `{selection.selection_hash}`",
        "",
        "## Orçamento e critérios predefinidos",
        "",
        f"- Portfólios tentados: {selection.attempted_portfolios}",
        f"- Oportunidades processadas nas tentativas: {selection.evaluated_opportunities}",
        f"- Budget máximo: {selection.config.attempted_portfolio_budget}",
        f"- Sobreposição pareada máxima: {_text(selection.config.max_pairwise_event_overlap)}",
        f"- Concentração máxima por ativo: {_text(selection.config.max_asset_concentration)}",
        f"- Concentração máxima por hora UTC: {_text(selection.config.max_hour_concentration)}",
        f"- Ganho marginal mínimo: {selection.config.min_marginal_executed}",
        "",
        "## Resultado de desenvolvimento",
        "",
        f"- Receitas selecionadas: {len(selection.selected_keys)}",
        f"- Operações executáveis: {metrics.sample_size}",
        "- Frequência de desenvolvimento: "
        f"{_text(metrics.executable_operations_per_day)} operações/dia",
        f"- p_hat: {_text(metrics.p_hat)}",
        f"- Wilson 95%: [{_text(metrics.wilson_lower)}, {_text(metrics.wilson_upper)}]",
        f"- Payout médio: {_optional_text(metrics.mean_payout_return_ratio)}",
        f"- Payout de equilíbrio: {_optional_text(metrics.break_even_payout_return_ratio)}",
        f"- EV estimado/stake: {_optional_text(metrics.estimated_ev_per_stake)}",
        f"- EV com -0,5 pp: {_optional_text(metrics.sensitivity_ev_minus_half_pp)}",
        f"- EV com -1,0 pp: {_optional_text(metrics.sensitivity_ev_minus_one_pp)}",
        f"- Drawdown máximo: {_text(metrics.maximum_drawdown)}",
        f"- Pior sequência de perdas: {metrics.worst_loss_streak}",
        "",
        "## Comparação incremental",
        "",
        "| Solicitado | Disponível | Operações | Ops/dia | EV/stake |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in selection.size_comparisons:
        lines.append(
            f"| {item.requested_size} | {item.actual_size} | {item.metrics.sample_size} | "
            f"{_text(item.metrics.executable_operations_per_day)} | "
            f"{_optional_text(item.metrics.estimated_ev_per_stake)} |"
        )
    lines.extend(["", "## Holdout de portfólio", ""])
    if holdout is None:
        lines.append("- Não aberto. A seleção permanece congelada e não publicável.")
    else:
        holdout_metrics = holdout.metrics
        lines.extend(
            [
                f"- Snapshot: `{holdout.holdout_snapshot_id}`",
                f"- Veredito: `{holdout.reason.value}`",
                f"- Operações OOS: {holdout_metrics.sample_size}",
                "- Frequência OOS: "
                f"{_text(holdout_metrics.executable_operations_per_day)} operações/dia",
                f"- Wilson 95% OOS: [{_text(holdout_metrics.wilson_lower)}, "
                f"{_text(holdout_metrics.wilson_upper)}]",
                f"- EV OOS/stake: {_optional_text(holdout_metrics.estimated_ev_per_stake)}",
                f"- Drawdown máximo OOS: {_text(holdout_metrics.maximum_drawdown)}",
                f"- Pior sequência de perdas OOS: {holdout_metrics.worst_loss_streak}",
            ]
        )
    lines.extend(
        [
            "",
            "## Proveniência",
            "",
            f"- Tipos de dataset: {', '.join(selection.dataset_kinds) or 'nenhum'}",
            "- Histórico real, pesquisa sintética e validação externa permanecem separados.",
            "- Validação externa: não executada nesta seleção offline.",
            "- O relatório mede evidência histórica; não oferece garantia de resultado futuro.",
            "- O rascunho não é assinado nem publicado automaticamente.",
        ]
    )
    return "\n".join(lines)


def save_selection_artifacts(
    selection: FrozenPortfolioSelection,
    holdout: HoldoutEvaluation,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Persist a report and unsigned draft locally; no network action is performed."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "portfolio_selection.md"
    draft_path = output_dir / "manifest_draft.json"
    report_path.write_text(selection_report_markdown(selection, holdout), encoding="utf-8")
    draft = build_manifest_draft(selection, holdout)
    draft_path.write_text(
        json.dumps(draft.to_dict(), sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report_path, draft_path


def _opportunities(recipes: Sequence[DevelopmentRecipe]) -> tuple[PortfolioOpportunity, ...]:
    return tuple(item for recipe in recipes for item in recipe.opportunities)


def _maximum_overlap(
    candidate: DevelopmentRecipe, selected: Sequence[DevelopmentRecipe]
) -> Decimal:
    if not selected:
        return ZERO
    candidate_events = _event_set(candidate.opportunities)
    return max(_jaccard(candidate_events, _event_set(item.opportunities)) for item in selected)


def _event_set(opportunities: Sequence[PortfolioOpportunity]) -> frozenset[tuple[str, int]]:
    return frozenset((item.asset, item.signal_ts) for item in opportunities)


def _jaccard(left: frozenset[tuple[str, int]], right: frozenset[tuple[str, int]]) -> Decimal:
    union = left | right
    if not union:
        return ZERO
    return Decimal(len(left & right)) / Decimal(len(union))


def _maximum_concentration(values: Mapping[str, Decimal]) -> Decimal:
    return max(values.values(), default=ZERO)


def _hour_concentration(result: PortfolioReplayResult) -> Decimal:
    if not result.executed_total:
        return ZERO
    return Decimal(max(result.executed_by_utc_hour.values(), default=0)) / Decimal(
        result.executed_total
    )


def _wilson_upper(wins: int, n: int) -> Decimal:
    if n <= 0:
        return ZERO
    d_n = Decimal(n)
    p_hat = Decimal(wins) / d_n
    z2 = DEFAULT_Z**2
    denominator = ONE + z2 / d_n
    center = p_hat + z2 / (Decimal(2) * d_n)
    variance = p_hat * (ONE - p_hat) / d_n + z2 / (Decimal(4) * d_n**2)
    value = (center + DEFAULT_Z * variance.sqrt()) / denominator
    return min(ONE, value)


def _ev(p_hat: Decimal, payout: Decimal | None) -> Decimal | None:
    if payout is None:
        return None
    return p_hat * payout - (ONE - p_hat)


def _maximum_drawdown(result: PortfolioReplayResult) -> Decimal:
    peak = result.config.starting_capital
    maximum = ZERO
    for trade in result.trades:
        peak = max(peak, trade.balance_after)
        maximum = max(maximum, peak - trade.balance_after)
    return maximum


def _worst_loss_streak(result: PortfolioReplayResult) -> int:
    current = 0
    worst = 0
    for trade in result.trades:
        current = 0 if trade.won else current + 1
        worst = max(worst, current)
    return worst


def _config_payload(config: PortfolioSelectionConfig) -> dict[str, object]:
    replay = config.replay
    return {
        "attempted_portfolio_budget": config.attempted_portfolio_budget,
        "max_asset_concentration": _text(config.max_asset_concentration),
        "max_hour_concentration": _text(config.max_hour_concentration),
        "max_pairwise_event_overlap": _text(config.max_pairwise_event_overlap),
        "max_recipes": config.max_recipes,
        "method_version": config.method_version,
        "min_holdout_ev_per_stake": _text(config.min_holdout_ev_per_stake),
        "min_holdout_trades": config.min_holdout_trades,
        "min_holdout_wilson_lower": _text(config.min_holdout_wilson_lower),
        "min_marginal_executed": config.min_marginal_executed,
        "replay": {
            "cooldown_after_loss_s": replay.cooldown_after_loss_s,
            "execution_delay_s": replay.execution_delay_s,
            "max_consecutive_losses": replay.max_consecutive_losses,
            "period_end_ts": replay.period_end_ts,
            "period_start_ts": replay.period_start_ts,
            "seed": replay.seed,
            "signal_ttl_s": replay.signal_ttl_s,
            "snapshot_id": replay.snapshot_id,
            "stake": _text(replay.stake),
            "starting_capital": _text(replay.starting_capital),
            "stop_loss_abs": _optional_text(replay.stop_loss_abs),
            "take_profit_abs": _optional_text(replay.take_profit_abs),
        },
        "target_sizes": list(config.target_sizes),
    }


def _stable_hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _text(value: Decimal) -> str:
    return format(value, "f")


def _optional_text(value: Decimal | None) -> str:
    return "n/a" if value is None else _text(value)
