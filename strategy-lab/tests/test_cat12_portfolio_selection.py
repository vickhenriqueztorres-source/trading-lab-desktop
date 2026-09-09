"""CAT-12: deterministic offline selection before one-shot portfolio holdout."""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from strategy_lab.research.portfolio_replay import PortfolioOpportunity, PortfolioReplayConfig
from strategy_lab.research.portfolio_selection import (
    DevelopmentRecipe,
    FrozenPortfolioSelection,
    HoldoutEvaluation,
    HoldoutLedger,
    HoldoutReason,
    PortfolioSelectionConfig,
    SelectionReason,
    build_manifest_draft,
    evaluate_frozen_holdout,
    portfolio_metrics,
    save_selection_artifacts,
    select_portfolio,
    selection_report_markdown,
)

BASE_TS = 1_704_067_200


def _op(
    key: str,
    offset: int,
    *,
    asset: str = "EURUSD-OTC",
    won: bool = True,
    payout: str = "0.87",
) -> PortfolioOpportunity:
    return PortfolioOpportunity(
        recipe_key=key,
        asset=asset,
        timeframe_s=60,
        signal_ts=BASE_TS + offset,
        direction="call",
        payout_return_ratio=Decimal(payout),
        won=won,
        duration_s=60,
    )


def _recipe(
    key: str,
    offsets: tuple[int, ...],
    *,
    approved: bool = True,
    robustness: str = "0.80",
    asset: str = "EURUSD-OTC",
    wins: tuple[bool, ...] | None = None,
    dataset_kind: Literal["real_market", "synthetic"] = "real_market",
) -> DevelopmentRecipe:
    outcomes = wins or tuple(True for _ in offsets)
    return DevelopmentRecipe(
        recipe_key=key,
        evidence_ref=f"evidence:{key}",
        individually_approved=approved,
        individual_reason="" if approved else "WILSON_LOWER_BELOW_THRESHOLD",
        robustness_score=Decimal(robustness),
        opportunities=tuple(
            _op(key, offset, asset=asset, won=won)
            for offset, won in zip(offsets, outcomes, strict=True)
        ),
        dataset_kind=dataset_kind,
    )


def _config(**changes: object) -> PortfolioSelectionConfig:
    values: dict[str, object] = {
        "replay": PortfolioReplayConfig(
            snapshot_id="development-v1",
            seed=1201,
            period_start_ts=BASE_TS,
            period_end_ts=BASE_TS + 86_400,
        ),
        "max_pairwise_event_overlap": Decimal("0.60"),
        "max_asset_concentration": Decimal("1"),
        "max_hour_concentration": Decimal("1"),
        "min_holdout_trades": 4,
        "min_holdout_wilson_lower": Decimal("0.30"),
        "min_holdout_ev_per_stake": Decimal("0"),
    }
    values.update(changes)
    return PortfolioSelectionConfig(**values)  # type: ignore[arg-type]


def _evaluate_holdout(
    selection: FrozenPortfolioSelection,
    opportunities: tuple[PortfolioOpportunity, ...],
    snapshot_id: str,
    ledger: HoldoutLedger,
) -> HoldoutEvaluation:
    return evaluate_frozen_holdout(
        selection,
        opportunities,
        holdout_snapshot_id=snapshot_id,
        holdout_period_start_ts=BASE_TS + 3600,
        holdout_period_end_ts=BASE_TS + 7200,
        ledger=ledger,
    )


def test_individual_gates_run_before_portfolio_composition() -> None:
    rejected = _recipe("rejected", tuple(range(0, 1200, 120)), approved=False)
    approved = _recipe("approved", (0, 120, 240))

    result = select_portfolio([rejected, approved], _config())

    assert result.selected_keys == ("approved",)
    assert result.excluded["rejected"] == SelectionReason.INDIVIDUAL_GATE_FAILED


def test_synthetic_and_real_market_evidence_cannot_be_mixed() -> None:
    with pytest.raises(ValueError, match="RES_PORTFOLIO_DATASET_KIND_MIXED"):
        select_portfolio(
            [
                _recipe("real", (0,), dataset_kind="real_market"),
                _recipe("synthetic", (120,), dataset_kind="synthetic"),
            ],
            _config(),
        )


def test_selection_is_reproducible_and_input_order_independent() -> None:
    recipes = [
        _recipe("a", (0, 120, 240), robustness="0.90"),
        _recipe("b", (360, 480), robustness="0.85", asset="GBPUSD-OTC"),
        _recipe("c", (600,), robustness="0.70", asset="USDJPY-OTC"),
    ]

    first = select_portfolio(recipes, _config())
    second = select_portfolio(list(reversed(recipes)), _config())

    assert first.selected_keys == ("a", "b", "c")
    assert first.selection_hash == second.selection_hash
    assert first.config.config_hash == second.config.config_hash
    assert first.attempted_portfolios <= first.config.attempted_portfolio_budget


def test_duplicate_event_recipe_does_not_fake_marginal_frequency() -> None:
    primary = _recipe("primary", (0, 120, 240), robustness="0.90")
    duplicate = _recipe("duplicate", (0, 120, 240), robustness="0.99")
    complement = _recipe("complement", (360, 480), asset="GBPUSD-OTC")

    result = select_portfolio([duplicate, complement, primary], _config())

    assert len({"primary", "duplicate"} & set(result.selected_keys)) == 1
    blocked_key = ({"primary", "duplicate"} - set(result.selected_keys)).pop()
    assert result.excluded[blocked_key] == SelectionReason.CORRELATION_LIMIT
    assert result.development_result.executed_total == 5


def test_predeclared_asset_concentration_can_limit_library() -> None:
    recipes = [
        _recipe("eur-a", (0, 120, 240), asset="EURUSD-OTC"),
        _recipe("eur-b", (360, 480), asset="EURUSD-OTC"),
        _recipe("gbp", (600, 720, 840), asset="GBPUSD-OTC"),
    ]

    result = select_portfolio(
        recipes,
        _config(max_asset_concentration=Decimal("0.60")),
    )

    assert "eur-b" not in result.selected_keys
    assert result.excluded["eur-b"] == SelectionReason.ASSET_CONCENTRATION_LIMIT


def test_incremental_comparison_reports_requested_10_20_30_50_100() -> None:
    recipes = [
        _recipe(f"r{index:02d}", (index * 120,), asset=f"R_{index:02d}") for index in range(12)
    ]

    result = select_portfolio(recipes, _config())

    assert tuple(item.requested_size for item in result.size_comparisons) == (
        10,
        20,
        30,
        50,
        100,
    )
    assert tuple(item.actual_size for item in result.size_comparisons) == (10, 12, 12, 12, 12)
    assert result.size_comparisons[0].metrics.sample_size == 10
    assert result.size_comparisons[-1].metrics.sample_size == 12


def test_holdout_is_opened_once_and_failure_cannot_be_retuned() -> None:
    selection = select_portfolio([_recipe("a", (0, 120, 240, 360))], _config())
    ledger = HoldoutLedger()
    failing = tuple(_op("a", 3600 + index * 120, won=False) for index in range(4))

    evaluation = _evaluate_holdout(
        selection,
        failing,
        "holdout-v1",
        ledger,
    )

    assert not evaluation.passed
    assert evaluation.reason == HoldoutReason.WILSON_BELOW_THRESHOLD
    with pytest.raises(RuntimeError, match="RES_PORTFOLIO_HOLDOUT_ALREADY_OPENED"):
        _evaluate_holdout(
            selection,
            tuple(_op("a", 7200 + index * 120) for index in range(4)),
            "holdout-v1",
            ledger,
        )


def test_metrics_and_manifest_draft_are_evidence_linked_but_never_published() -> None:
    selection = select_portfolio(
        [_recipe("a", (0, 120, 240, 360), wins=(True, True, True, False))],
        _config(),
    )
    holdout_ops = tuple(
        _op("a", 3600 + index * 120, won=won) for index, won in enumerate((True, True, True, False))
    )
    evaluation = _evaluate_holdout(
        selection,
        holdout_ops,
        "holdout-v2",
        HoldoutLedger(),
    )
    metrics = portfolio_metrics(evaluation.result)
    draft = build_manifest_draft(selection, evaluation)

    assert evaluation.passed
    assert metrics.p_hat == Decimal("0.75")
    assert metrics.break_even_payout_return_ratio == Decimal("1") / Decimal("3")
    assert metrics.estimated_ev_per_stake == Decimal("0.4025")
    assert metrics.worst_loss_streak == 1
    assert draft.publish_automatically is False
    assert draft.signature is None
    assert draft.entries[0]["evidence_ref"] == selection.evidence_refs["a"]


def test_report_discloses_cost_provenance_uncertainty_and_no_auto_publish() -> None:
    selection = select_portfolio(
        [_recipe("synthetic-a", (0, 120), dataset_kind="synthetic")],
        _config(),
    )

    report = selection_report_markdown(selection)

    assert "Portfólios tentados" in report
    assert "Oportunidades processadas" in report
    assert "Wilson 95%" in report
    assert "Payout de equilíbrio" in report
    assert "Validação externa: não executada" in report
    assert "não oferece garantia" in report
    assert "não é assinado nem publicado automaticamente" in report


def test_local_artifacts_include_holdout_but_do_not_publish(tmp_path: Path) -> None:
    selection = select_portfolio([_recipe("a", (0, 120, 240, 360))], _config())
    holdout = _evaluate_holdout(
        selection,
        tuple(_op("a", 3600 + index * 120) for index in range(4)),
        "holdout-artifact-v1",
        HoldoutLedger(),
    )

    report_path, draft_path = save_selection_artifacts(selection, holdout, tmp_path)

    assert "Frequência OOS" in report_path.read_text(encoding="utf-8")
    draft = draft_path.read_text(encoding="utf-8")
    assert '"publish_automatically": false' in draft
    assert '"signature": null' in draft


def test_selection_module_has_no_publisher_backend_or_broker_import() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "strategy_lab"
        / "research"
        / "portfolio_selection.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = ("strategy_lab.publish", "strategy_lab.collect", "supabase", "psycopg")
    assert not any(name.startswith(forbidden) for name in imported)
