"""Tests for the complete 10-step research pipeline and acceptance criteria.
R-RES-2, R-RES-3, R-RES-10.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from strategy_lab.cli import main
from strategy_lab.research.payout_lookup import PayoutLookup, PayoutPoint
from strategy_lab.research.runner import (
    SanityCheckFailedError,
    run_research_pipeline,
)
from strategy_lab.research.synthetic import (
    BASE_TS,
    edge_series,
    make_injected_edge_candidate,
    random_walk,
    register_synthetic_primitives,
)


def _payout_lookup(payout: str = "0.87") -> PayoutLookup:
    return PayoutLookup(
        [
            PayoutPoint(
                "EURUSD-OTC",
                BASE_TS - BASE_TS % 3600 + offset * 3600,
                Decimal(payout),
                1,
            )
            for offset in range(50)
        ]
    )


def test_research_pipeline_synthetic_approves_only_injected_edge(tmp_path: Path) -> None:
    """Critério de aceite 1: sobre dados sintéticos com 1 edge injetado, aprova somente ele."""
    register_synthetic_primitives()
    seed = 1
    candles = edge_series(seed=seed, length=2000, win_probability_pct=65)
    lookup = _payout_lookup("0.87")

    edge_cand = make_injected_edge_candidate("EURUSD-OTC")

    # Pool with 1 edge candidate and competing non-edge candidates
    pool = [edge_cand]

    result = run_research_pipeline(
        candles,
        lookup,
        run_id="test_run_edge_only",
        assets=["EURUSD-OTC"],
        seed=seed,
        max_candidates=10,
        output_dir=tmp_path,
        override_candidates=pool,
        min_oos_trades=50,
        enforce_holdout_pass=False,
    )

    assert result.status == "ok"
    assert result.candidates_count == 1
    assert result.approved_count == 1
    assert result.reports[0].approval.approved is True
    assert result.reports[0].candidate == edge_cand
    assert result.reports[0].score.wilson_lower >= Decimal("0.55")


def test_research_step8_sanity_check_random_walk_approves_zero(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Critério de aceite 2: Sanidade embaralhada / random walk aprova ZERO candidatos (log)."""
    register_synthetic_primitives()
    seed = 42
    lookup = _payout_lookup("0.87")

    # In random walk without edge, even the edge candidate has ~50% win rate
    rw_candles = random_walk(seed=seed, length=2000)
    edge_cand = make_injected_edge_candidate("EURUSD-OTC")

    with caplog.at_level("INFO"):
        result = run_research_pipeline(
            rw_candles,
            lookup,
            run_id="test_run_rw_sanity",
            assets=["EURUSD-OTC"],
            seed=seed,
            max_candidates=10,
            output_dir=tmp_path,
            override_candidates=[edge_cand],
            min_oos_trades=50,
            enforce_holdout_pass=False,
        )

    assert result.status == "ok"
    assert result.approved_count == 0
    # Confirms step 8 logged zero approved
    assert "Step 8 Sanity check on random walk: 0 approved" in caplog.text


def test_research_cli_end_to_end_with_seed_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Critério de aceite CLI: `strategy-lab research --seed 1` executa e aprova somente o edge."""
    code = main(["research", "--seed", "1", "--output-dir", str(tmp_path)])
    captured = capsys.readouterr().out
    assert code == 0

    payload = json.loads(captured.strip().splitlines()[-1])
    assert payload["event"] == "strategy_lab_research_completed"
    assert payload["status"] == "ok"
    assert payload["approved_count"] == 1
    assert Path(payload["ranking_md"]).exists()
    assert Path(payload["candidates_json"]).exists()

    # Verify ranking.md contains "Novas oportunidades" section
    ranking_content = Path(payload["ranking_md"]).read_text(encoding="utf-8")
    assert "## Novas oportunidades" in ranking_content
    assert "Taxa de acerto validada" in ranking_content
    assert "Margem de segurança" in ranking_content
    assert "Pior sequência de perdas" in ranking_content
    assert "Resultado em 1.000 ops" in ranking_content


def test_research_runner_aborts_run_if_sanity_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passo 8: se a série embaralhada aprovar qualquer candidato, o run é abortado."""
    register_synthetic_primitives()
    seed = 1
    candles = edge_series(seed=seed, length=2000, win_probability_pct=65)
    lookup = _payout_lookup("0.87")
    edge_cand = make_injected_edge_candidate("EURUSD-OTC")

    # Mock random_walk to return edge_series during sanity step to force a failure
    from strategy_lab.research import runner as runner_mod

    def rigged_random_walk(*args, **kwargs):
        return edge_series(seed=seed, length=2000, win_probability_pct=65)

    monkeypatch.setattr(runner_mod, "random_walk", rigged_random_walk)

    with pytest.raises(SanityCheckFailedError, match="Sanity check failed.*Run test_abort aborted"):
        run_research_pipeline(
            candles,
            lookup,
            run_id="test_abort",
            assets=["EURUSD-OTC"],
            seed=seed,
            max_candidates=10,
            output_dir=tmp_path,
            override_candidates=[edge_cand],
            min_oos_trades=50,
            enforce_holdout_pass=False,
        )
