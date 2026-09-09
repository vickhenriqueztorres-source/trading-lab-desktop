"""CAT-07: grammar is broad, bounded, deterministic, and executor-compatible."""

from __future__ import annotations

from decimal import Decimal

from primitives.base import decimal_range
from strategy_lab.research.grammar import (
    DEFAULT_TRIAL_BUDGET,
    ExecutorCapabilities,
    enumerate_candidates,
    generate_param_values,
)


def test_default_trial_budget_is_named_and_bounded() -> None:
    """R-RES-3: default CAT-07 trial budget starts in the 300-500 range."""
    assert 300 <= DEFAULT_TRIAL_BUDGET <= 500

    result = enumerate_candidates(
        assets=["EURUSD-OTC", "GBPUSD-OTC"],
        timeframes=["M1", "M5", "M15"],
        hours_slots=[(0, 6), (6, 10), (10, 13), (13, 16), (16, 21), (0, 24)],
        seed=77,
    )

    assert len(result.candidates) <= DEFAULT_TRIAL_BUDGET
    assert result.audit_report()["sampled_candidates"] == len(result.candidates)
    assert result.audit_report()["trial_budget"] == DEFAULT_TRIAL_BUDGET
    assert result.audit_report()["diversity"]


def test_same_inputs_same_bounded_sample() -> None:
    """R-RES-3: streaming sample is deterministic for seed and inputs."""
    args = {
        "assets": ["EURUSD-OTC", "GBPUSD-OTC"],
        "timeframes": ["M1", "M5"],
        "hours_slots": [(0, 6), (6, 10), (10, 13)],
        "max_candidates": 35,
        "seed": 1234,
    }

    first = enumerate_candidates(**args)
    second = enumerate_candidates(**args)

    assert [candidate.stable_hash() for candidate in first.candidates] == [
        candidate.stable_hash() for candidate in second.candidates
    ]
    assert len(first.candidates) == 35
    assert first.total_candidates == first.eligible_candidates


def test_capabilities_prune_before_sample() -> None:
    """R-RES-3/R-ISO-6: candidates outside executor capabilities are never sampled."""
    caps = ExecutorCapabilities(
        families=frozenset({"F1", "F5"}),
        timeframes=frozenset({"M1"}),
        max_warmup_candles=80,
        tick_volume=False,
        max_trials_per_experiment=40,
        supported_assets=frozenset({"EURUSD-OTC"}),
    )

    result = enumerate_candidates(
        assets=["EURUSD-OTC", "GBPUSD-OTC"],
        timeframes=["M1", "M5"],
        hours_slots=[(0, 6), (6, 10)],
        max_candidates=200,
        seed=9,
        executor_capabilities=caps,
    )

    assert 0 < len(result.candidates) <= 40
    assert {candidate.family for candidate in result.candidates} <= {"F1", "F5"}
    assert {candidate.tf for candidate in result.candidates} == {"M1"}
    assert {candidate.asset for candidate in result.candidates} == {"EURUSD-OTC"}
    assert result.discarded_by_reason is not None
    assert result.discarded_by_reason["ASSET_UNSUPPORTED"] > 0
    assert result.discarded_by_reason["TIMEFRAME_UNSUPPORTED"] > 0


def test_f5_is_generated_only_as_canonical_family() -> None:
    """R-RES-3: F5 exists because it is in the public contract, without enabling random trios."""
    result = enumerate_candidates(
        assets=["EURUSD-OTC"],
        timeframes=["M1"],
        hours_slots=[(0, 6)],
        max_candidates=200,
        seed=5,
        executor_capabilities=ExecutorCapabilities(
            families=frozenset({"F5"}),
            timeframes=frozenset({"M1"}),
            max_trials_per_experiment=200,
        ),
    )

    assert {candidate.family for candidate in result.candidates} == {"F5"}
    assert {
        (candidate.regime, candidate.trigger, candidate.confirm) for candidate in result.candidates
    } == {("session_window", "quadrant_majority", "rsi_extreme")}


def test_absolute_level_family_requires_asset_profile() -> None:
    """R-RES-3: F3 level-touch candidates need asset-specific levels, not 99/101 defaults."""
    caps = ExecutorCapabilities(
        families=frozenset({"F3"}),
        timeframes=frozenset({"M1"}),
        max_trials_per_experiment=20,
    )
    missing = enumerate_candidates(
        assets=["EURUSD"],
        timeframes=["M1"],
        hours_slots=[(0, 6)],
        max_candidates=20,
        seed=1,
        executor_capabilities=caps,
    )
    assert missing.candidates == []
    assert missing.discarded_by_reason is not None
    assert missing.discarded_by_reason["ABSOLUTE_LEVEL_PROFILE_REQUIRED"] > 0

    profiled = enumerate_candidates(
        assets=["EURUSD"],
        timeframes=["M1"],
        hours_slots=[(0, 6)],
        max_candidates=20,
        seed=1,
        executor_capabilities=caps,
        level_profiles={
            "EURUSD": [
                {
                    "support": Decimal("1.0900"),
                    "resistance": Decimal("1.1100"),
                    "tolerance": Decimal("0.0005"),
                }
            ]
        },
    )
    assert profiled.candidates
    assert all(
        candidate.params["level_touch"]["support"] == Decimal("1.0900")
        and candidate.params["level_touch"]["resistance"] == Decimal("1.1100")
        for candidate in profiled.candidates
    )


def test_min_mid_max_values_stay_on_declared_grid() -> None:
    """R-MAN-3/R-RES-3: min/mid/max pruning never emits a value outside the declared step."""
    values = generate_param_values(decimal_range("0", "1", "0.3"))

    assert values == [Decimal("0"), Decimal("0.3"), Decimal("0.6"), Decimal("0.9")]
