from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.domain.market import MarketTick
from packages.domain.models import Broker
from packages.strategies.deriv_digits import (
    DerivDigitShadowEngine,
    DerivMultiAssetShadowRadar,
    DigitAssetShadowState,
    ParityRegimeEdgeStrategy,
    SelectiveDiffersEdgeStrategy,
    ShadowSignalState,
    TailProbabilityEdgeStrategy,
)

_START = datetime(2026, 1, 1, tzinfo=UTC)


def _ticks(digits: list[int], *, symbol: str = "R_100") -> tuple[MarketTick, ...]:
    return tuple(
        MarketTick(
            Broker.DERIV,
            symbol,
            1_800_000_000 + index,
            Decimal(f"100.0{digit}"),
            _START + timedelta(seconds=index),
            f"sub-{index}",
            "TEST",
        )
        for index, digit in enumerate(digits)
    )


def test_tail_probability_edge_requires_three_windows_and_emits_over_signal() -> None:
    ticks = _ticks([digit for _ in range(250) for digit in (9, 0)])

    decision = TailProbabilityEdgeStrategy().evaluate(ticks)

    assert decision.state is ShadowSignalState.SHADOW_SIGNAL
    assert decision.contract_type == "DIGITOVER"
    assert decision.direction == "OVER"
    assert decision.barrier == 4
    assert decision.estimated_probability_pct is not None
    assert decision.estimated_probability_pct > decision.required_probability_pct


def test_selective_differs_uses_same_least_likely_digit_across_windows() -> None:
    ticks = _ticks([digit for _ in range(250) for digit in (9, 0)])

    decision = SelectiveDiffersEdgeStrategy().evaluate(ticks)

    assert decision.state is ShadowSignalState.SHADOW_SIGNAL
    assert decision.contract_type == "DIGITDIFF"
    assert decision.barrier == 0
    assert decision.direction == "DIFFERS 0"


def test_parity_regime_edge_emits_conditional_odd_signal() -> None:
    ticks = _ticks([digit for _ in range(250) for digit in (1, 2)])

    decision = ParityRegimeEdgeStrategy().evaluate(ticks)

    assert decision.state is ShadowSignalState.SHADOW_SIGNAL
    assert decision.contract_type == "DIGITODD"
    assert decision.direction == "ODD"
    assert decision.barrier is None


def test_uniform_random_ticks_do_not_invent_a_conservative_advantage() -> None:
    generator = random.Random(20260824)
    ticks = _ticks([generator.randrange(10) for _ in range(500)])

    decisions = (
        TailProbabilityEdgeStrategy().evaluate(ticks),
        SelectiveDiffersEdgeStrategy().evaluate(ticks),
        ParityRegimeEdgeStrategy().evaluate(ticks),
    )

    assert all(item.state is ShadowSignalState.MONITORING for item in decisions)


def test_shadow_engine_exposes_three_digit_strategies_and_measured_latency() -> None:
    times = iter((1_000, 10_000))
    engine = DerivDigitShadowEngine(monotonic_ns=lambda: next(times))

    engine.ingest_history("R_100", ticks=_ticks([digit for _ in range(250) for digit in (9, 0)]))
    projections = engine.projections()

    assert len(projections) == 3
    assert {item.lifecycle_status for item in projections} == {"RESEARCH_SHADOW"}
    assert {item.signal_state for item in projections} == {ShadowSignalState.SHADOW_SIGNAL}
    assert {item.analysis_latency_microseconds for item in projections} == {9}


def test_shadow_engine_duplicate_tick_does_not_recompute_or_duplicate_signal() -> None:
    times = iter((1_000, 5_000))
    engine = DerivDigitShadowEngine(monotonic_ns=lambda: next(times))
    ticks = _ticks([digit for _ in range(250) for digit in (9, 0)])
    engine.ingest_history("R_100", ticks=ticks)

    before = engine.projections()
    engine.ingest_tick(ticks[-1])

    assert engine.projections() == before


def test_multi_asset_radar_keeps_independent_buffers_and_selects_one_candidate() -> None:
    radar = DerivMultiAssetShadowRadar(("R_25", "R_100"))
    generator = random.Random(20260825)
    radar.ingest_history(
        "R_100",
        ticks=_ticks([digit for _ in range(250) for digit in (9, 0)], symbol="R_100"),
    )
    radar.ingest_history(
        "R_25",
        ticks=_ticks([generator.randrange(10) for _ in range(500)], symbol="R_25"),
    )

    ranking = radar.asset_ranking()
    by_symbol = {item.symbol: item for item in ranking}

    assert by_symbol["R_100"].state is DigitAssetShadowState.CANDIDATE
    assert by_symbol["R_100"].selected is True
    assert by_symbol["R_25"].state is DigitAssetShadowState.MONITORING
    assert by_symbol["R_25"].selected is False
    assert all(item.warmup_current == 500 for item in ranking)


def test_multi_asset_radar_abstains_when_no_conservative_candidate_exists() -> None:
    radar = DerivMultiAssetShadowRadar(("R_25", "R_100"))
    generator = random.Random(20260825)
    uniform = [generator.randrange(10) for _ in range(500)]
    radar.ingest_history("R_25", ticks=_ticks(uniform, symbol="R_25"))
    radar.ingest_history("R_100", ticks=_ticks(uniform, symbol="R_100"))

    ranking = radar.asset_ranking()

    assert {item.state for item in ranking} == {DigitAssetShadowState.MONITORING}
    assert not any(item.selected for item in ranking)


def test_multi_asset_radar_preserves_existing_symbol_engine_when_universe_changes() -> None:
    radar = DerivMultiAssetShadowRadar(("R_100",))
    radar.ingest_history(
        "R_100",
        ticks=_ticks([digit for _ in range(250) for digit in (9, 0)], symbol="R_100"),
    )

    before = radar.strategy_projections("R_100")
    radar.set_symbols(("R_25", "R_100"))

    assert radar.strategy_projections("R_100") == before
    assert radar.asset_ranking()[0].symbol == "R_100"
