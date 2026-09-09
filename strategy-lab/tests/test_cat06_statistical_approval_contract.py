"""CAT-06: statistical approval cannot use shortcuts or fabricated report fields."""

from __future__ import annotations

import ast
import json
from decimal import Decimal
from pathlib import Path

import pytest
from primitives import Candle
from strategy_lab.research.candidate import Candidate
from strategy_lab.research.dataset import (
    DATASET_SCHEMA_VERSION,
    DatasetIdentity,
    DatasetOrigin,
    DatasetQuality,
    DatasetSnapshot,
    synthetic_snapshot,
)
from strategy_lab.research.evidence import FakeEvidenceRepository
from strategy_lab.research.gates.multiple_testing import permutation_test
from strategy_lab.research.payout_lookup import PayoutLookup, PayoutPoint
from strategy_lab.research.runner import (
    _candidate_fdr_ranks,
    _translate_params_to_wire,
    run_research_pipeline,
)
from strategy_lab.research.synthetic import (
    BASE_TS,
    edge_series,
    make_injected_edge_candidate,
    register_synthetic_primitives,
)


def _payout_lookup() -> PayoutLookup:
    return PayoutLookup(
        [
            PayoutPoint(
                "EURUSD-OTC",
                BASE_TS - BASE_TS % 3600 + offset * 3600,
                Decimal("0.87"),
                1,
            )
            for offset in range(50)
        ]
    )


def _short_production_snapshot(candles: list[Candle]) -> DatasetSnapshot:
    return DatasetSnapshot(
        identity=DatasetIdentity(
            schema_version=DATASET_SCHEMA_VERSION,
            origin=DatasetOrigin.SUPABASE,
            source="supabase:staging:cat06-short-holdout",
            asset="EURUSD-OTC",
            timeframe_s=60,
            from_ts=candles[0].ts,
            to_ts=candles[-1].ts,
        ),
        quality=DatasetQuality(
            present=len(candles),
            expected=len(candles),
            coverage=Decimal("1"),
            unresolved_in_session_gaps=0,
            tick_volume_available=True,
            approval_eligible=True,
        ),
        fingerprint="sha256:" + "6" * 64,
    )


def test_payout_missing_is_explicit_failure_not_zero_trades(tmp_path: Path) -> None:
    register_synthetic_primitives()
    candles = edge_series(seed=1, length=2000, win_probability_pct=65)
    result = run_research_pipeline(
        candles,
        PayoutLookup(()),
        run_id="cat06_missing_payout",
        assets=["EURUSD-OTC"],
        output_dir=tmp_path,
        dataset_snapshot=synthetic_snapshot(candles, asset="EURUSD-OTC", seed=1),
        override_candidates=[make_injected_edge_candidate("EURUSD-OTC")],
        min_oos_trades=50,
    )

    assert result.reports[0].approval.reason == "RES_PAYOUT_MISSING"
    assert result.reports[0].approval.gate_results[0].gate_name == "payout_available"
    assert result.reports[0].approval.gate_results[0].passed is False


def test_production_eligible_run_rejects_short_holdout_fallback(tmp_path: Path) -> None:
    register_synthetic_primitives()
    candles = edge_series(seed=1, length=2000, win_probability_pct=65)

    with pytest.raises(ValueError, match="RES_HOLDOUT_WINDOW_TOO_SHORT"):
        run_research_pipeline(
            candles,
            _payout_lookup(),
            run_id="cat06_short_holdout",
            assets=["EURUSD-OTC"],
            output_dir=tmp_path,
            dataset_snapshot=_short_production_snapshot(candles),
            override_candidates=[make_injected_edge_candidate("EURUSD-OTC")],
            min_oos_trades=50,
            evidence_repository=FakeEvidenceRepository(),
            require_durable_evidence=True,
        )


def test_candidate_fdr_rank_uses_round_p_values_not_loop_position() -> None:
    register_synthetic_primitives()
    candles = edge_series(seed=1, length=2000, win_probability_pct=65)
    good = make_injected_edge_candidate("EURUSD-OTC")
    worse = Candidate(
        family="F1",
        regime="always_regime",
        trigger="body_trigger",
        confirm="body_confirm",
        tf="M1",
        asset="EURUSD-OTC",
    )
    from strategy_lab.research.replay_simulator import replay_candidate

    logs = {
        worse: replay_candidate(worse, candles, _payout_lookup()),
        good: replay_candidate(good, candles, _payout_lookup()),
    }
    ranks = _candidate_fdr_ranks(logs, Decimal("0.55"))

    assert set(ranks.values()) == {1, 2}
    assert ranks[good] in {1, 2}
    assert min(ranks.values()) == 1


def test_candidates_json_uses_real_gate_and_holdout_fields(tmp_path: Path) -> None:
    register_synthetic_primitives()
    candles = edge_series(seed=1, length=2000, win_probability_pct=65)
    result = run_research_pipeline(
        candles,
        _payout_lookup(),
        run_id="cat06_report",
        assets=["EURUSD-OTC"],
        output_dir=tmp_path,
        dataset_snapshot=synthetic_snapshot(candles, asset="EURUSD-OTC", seed=1),
        override_candidates=[make_injected_edge_candidate("EURUSD-OTC")],
        min_oos_trades=50,
        enforce_holdout_pass=False,
    )
    payload = json.loads(result.candidates_json_path.read_text(encoding="utf-8"))
    item = payload["candidates"][0]

    assert item["validated"]["windows_passed"] != "8/8"
    assert item["validated"]["gates_passed"] == (
        f"{sum(1 for gate in result.reports[0].approval.gate_results if gate.passed)}/"
        f"{len(result.reports[0].approval.gate_results)}"
    )
    assert item["validated"]["windows_passed"] != item["validated"]["gates_passed"]
    assert item["validated"]["holdout_passed"] is result.reports[0].holdout_passed


def test_unknown_family_is_not_translated_to_f1() -> None:
    with pytest.raises(ValueError, match="RES_UNKNOWN_FAMILY"):
        _translate_params_to_wire("UNKNOWN", {})


def test_permutation_and_pbo_approval_paths_do_not_call_float() -> None:
    assert permutation_test([True, False, True], seed=1).num_permutations == 1000
    for relative in (
        Path("tools/strategy_lab/research/gates/multiple_testing.py"),
        Path("tools/strategy_lab/research/gates/pbo.py"),
    ):
        tree = ast.parse(relative.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "float"
        ]
        assert calls == []
