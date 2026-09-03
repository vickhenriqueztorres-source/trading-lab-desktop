"""Tests for grammar enumeration, candidate constraints, and FDR tracking (R-RES-3)."""

from __future__ import annotations

from primitives.base import Category
from primitives.registry import REGISTRY
from strategy_lab.research.grammar import (
    INCOMPATIBLE,
    enumerate_candidates,
    is_compatible,
)


def test_grammar_never_generates_two_of_same_category() -> None:
    """R-RES-3: candidato tem 1 Regime, 1 Trigger e 1 Confirm; nunca 2 da mesma categoria."""
    res = enumerate_candidates(
        assets=["EURUSD-OTC"],
        timeframes=["M1"],
        hours_slots=[(0, 6)],
        max_candidates=200,
        seed=42,
    )

    assert len(res.candidates) > 0

    for cand in res.candidates:
        reg_cls = REGISTRY[cand.regime]
        trig_cls = REGISTRY[cand.trigger]
        conf_cls = REGISTRY[cand.confirm]

        assert reg_cls.category == Category.REGIME
        assert trig_cls.category == Category.TRIGGER
        assert conf_cls.category == Category.CONFIRM


def test_grammar_excludes_declared_incompatible_pairs() -> None:
    """R-RES-3: pares declarados em INCOMPATIBLE são excluídos."""
    assert is_compatible("session_window", "quadrant_majority", "rsi_extreme") is False
    assert is_compatible("adx", "bb_close_outside", "rsi_extreme") is True

    res = enumerate_candidates(
        assets=["EURUSD-OTC"],
        timeframes=["M1"],
        hours_slots=[(0, 6)],
        max_candidates=1000,
        seed=1,
        include_non_standard_families=True,
    )

    for cand in res.candidates:
        trio = {cand.regime, cand.trigger, cand.confirm}
        for inc_pair in INCOMPATIBLE:
            msg = f"Candidate {cand} contains incompatible pair {inc_pair}"
            assert not inc_pair.issubset(trio), msg


def test_grammar_respects_max_candidates_cap_and_deterministic_sampling() -> None:
    """R-RES-3: se exceder 5.000 (ou max_candidates), aplica amostragem determinística com seed."""
    cap = 50
    res1 = enumerate_candidates(
        assets=["EURUSD-OTC", "GBPUSD-OTC"],
        timeframes=["M1", "M5"],
        hours_slots=[(0, 6), (6, 10)],
        max_candidates=cap,
        seed=123,
    )
    res2 = enumerate_candidates(
        assets=["EURUSD-OTC", "GBPUSD-OTC"],
        timeframes=["M1", "M5"],
        hours_slots=[(0, 6), (6, 10)],
        max_candidates=cap,
        seed=123,
    )

    assert len(res1.candidates) <= cap
    assert res1.total_candidates >= len(res1.candidates)

    # Paridade determinística entre execuções com a mesma seed
    hashes1 = [c.stable_hash() for c in res1.candidates]
    hashes2 = [c.stable_hash() for c in res2.candidates]
    assert hashes1 == hashes2


def test_grammar_total_candidates_recorded_for_fdr() -> None:
    """R-RES-3: total_candidates é registrado e preservado mesmo após amostragem determinística."""
    res = enumerate_candidates(
        assets=["EURUSD-OTC"],
        timeframes=["M1"],
        hours_slots=[(0, 6)],
        max_candidates=10,
        seed=42,
    )

    assert res.total_candidates >= len(res.candidates)
    assert res.seed == 42
