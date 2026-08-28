from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.domain.market import MarketTick
from packages.domain.models import Broker
from packages.strategies.deriv_digits import (
    DerivDigitEnginePool,
    DerivDigitShadowEngine,
    DigitStrategyDecision,
    ShadowSignalState,
    default_digit_strategy_registry,
)
from packages.strategy_catalog import (
    DigitStrategyManifest,
    DigitStrategyRegistration,
    ParameterKind,
    ParameterSpec,
    ReleaseStatus,
    RiskClass,
)


def tick(symbol: str, epoch: int) -> MarketTick:
    return MarketTick(
        Broker.DERIV,
        symbol,
        epoch,
        Decimal(f"100.{epoch % 10}"),
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=epoch),
        f"s-{symbol}",
        "TEST",
    )


class FourthTestStrategy:
    strategy_id = "test-fourth-strategy"
    warmup_ticks = 1

    def evaluate(self, ticks: Sequence[MarketTick]) -> DigitStrategyDecision:
        symbol = ticks[-1].broker_symbol if ticks else "R_100"
        return DigitStrategyDecision(
            self.strategy_id,
            symbol,
            ShadowSignalState.MONITORING,
            "FOURTH_STRATEGY_EVALUATED",
            ticks[-1].epoch if ticks else None,
        )


def test_strategies_are_discovered_from_catalog() -> None:
    registry = default_digit_strategy_registry()
    registry.register(
        DigitStrategyRegistration(
            DigitStrategyManifest(
                strategy_id=FourthTestStrategy.strategy_id,
                version="test-only",
                display_name_pt_br="Quarta estratégia de teste",
                emitted_contracts=("DIGITDIFF",),
                parameter_schema=(ParameterSpec("window", ParameterKind.INTEGER, True),),
                risk_class=RiskClass.CONSERVATIVE,
                release_status=ReleaseStatus.DRAFT,
                warmup_ticks=1,
            ),
            FourthTestStrategy,
        )
    )
    engine = DerivDigitShadowEngine(registry=registry, symbol="R_100")
    engine.ingest_tick(tick("R_100", 1))
    projection = next(
        item
        for item in engine.projections()
        if str(item.strategy_id) == FourthTestStrategy.strategy_id
    )
    assert projection.reason_code == "FOURTH_STRATEGY_EVALUATED"
    assert projection.warmup_required == 1


def test_engine_per_symbol_warmup_completes_with_interleaved_ticks() -> None:
    symbols = ("R_10", "R_25", "R_50", "R_75", "R_100")
    pool = DerivDigitEnginePool(symbols)
    for epoch in range(1, 501):
        for symbol in symbols:
            pool.ingest_tick(tick(symbol, epoch))
    assert pool.active_engines == 5
    for symbol in symbols:
        projections = pool.strategy_projections(symbol)
        assert projections
        assert {item.warmup_current for item in projections} == {0, 500}
        assert ShadowSignalState.WARMING_UP not in {item.signal_state for item in projections}


def test_engine_rejects_foreign_symbol_tick_instead_of_clearing() -> None:
    engine = DerivDigitShadowEngine(symbol="R_100")
    engine.ingest_tick(tick("R_100", 1))
    with pytest.raises(ValueError, match="DIGIT_ENGINE_FOREIGN_SYMBOL"):
        engine.ingest_tick(tick("R_25", 2))
    assert engine.projections()[0].warmup_current == 1


def test_engine_pool_discards_engine_on_unsubscribe_without_leak() -> None:
    pool = DerivDigitEnginePool(("R_100",))
    pool.ingest_tick(tick("R_100", 1))
    assert pool.active_engines == 1
    pool.unsubscribe("R_100")
    assert pool.active_engines == 0
    assert pool.symbols == ()


def test_max_tracked_symbols_is_enforced() -> None:
    with pytest.raises(ValueError, match="exceeds bound"):
        DerivDigitEnginePool(tuple(f"R_{index}" for index in range(13)))


def test_saturation_event_is_emitted_without_dropping_decisions() -> None:
    values = iter((0, 25_000_000, 50_000_000, 75_000_000))
    events: list[dict[str, int]] = []
    pool = DerivDigitEnginePool(
        ("R_100",),
        evaluation_budget_microseconds=20_000,
        monotonic_ns=lambda: next(values),
        saturation_notifier=events.append,
    )
    pool.ingest_tick(tick("R_100", 1))
    assert events == [
        {
            "budget_microseconds": 20_000,
            "cycle_microseconds": 25_000,
            "active_engines": 1,
            "evaluation_stride": 2,
        }
    ]
    assert pool.strategy_projections("R_100")[0].warmup_current == 1


def test_per_strategy_telemetry_tracks_arbitration_by_symbol() -> None:
    pool = DerivDigitEnginePool(("R_100",))
    pool.ingest_history(
        "R_100",
        ticks=tuple(tick("R_100", epoch) for epoch in range(1, 501)),
    )
    pool.record_arbitration(
        "R_100:tail-probability-edge:500",
        ("R_100:selective-differs-edge:500",),
    )
    projections = {str(item.strategy_id): item for item in pool.strategy_projections("R_100")}
    assert projections["tail-probability-edge"].signals_executed_total == 1
    assert projections["selective-differs-edge"].signals_lost_to_arbitration_total == 1
