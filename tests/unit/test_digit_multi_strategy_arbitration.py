from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from apps.core.deriv_auto_trader import DerivDigitAutoTrader
from apps.core.digit_risk_config import DigitRiskConfig, StrategySelectionMode
from packages.signal_arbitration import (
    RankedRejectionReason,
    RankedSignalCandidate,
    SignalArbiter,
)
from packages.strategies.deriv_digits import DerivDigitStrategyId
from tests.unit.test_deriv_auto_trader import _Runtime, _telemetry


def candidate(strategy_id: str, margin: str, sample: int) -> RankedSignalCandidate:
    return RankedSignalCandidate(
        signal_id=f"{strategy_id}-signal",
        strategy_id=strategy_id,
        symbol="R_100",
        conservative_margin=Decimal(margin),
        conditional_sample=sample,
    )


def test_arbitration_is_deterministic_and_audited() -> None:
    arbiter = SignalArbiter(None)
    candidates = (
        candidate("z-strategy", "3.00", 100),
        candidate("b-strategy", "4.00", 90),
        candidate("a-strategy", "4.00", 90),
    )
    first = arbiter.arbitrate_ranked(candidates)
    second = arbiter.arbitrate_ranked(tuple(reversed(candidates)))
    assert first.winner_signal_id == second.winner_signal_id == "a-strategy-signal"
    assert {item.reason for item in first.rejected} == {
        RankedRejectionReason.LOST_TO_HIGHER_MARGIN,
        RankedRejectionReason.LOST_TO_STABLE_STRATEGY_ID,
    }
    assert arbiter.ranked_audit[-1] == second


def test_multiple_enabled_strategies_produce_single_inflight_order() -> None:
    runtime = _Runtime()
    runtime.risk_ledger.digit_config = DigitRiskConfig(
        auto_select_symbol=True,
        selection_mode=StrategySelectionMode.MULTI,
    )
    base = _telemetry()
    template = base.synthetic_strategies[0]
    matrix = tuple(
        replace(
            template,
            strategy_id=strategy_id,
            last_signal_symbol=symbol,
            last_signal_epoch=500,
            estimated_probability_pct=Decimal("80") + Decimal(index),
            required_probability_pct=Decimal("72"),
            conditional_sample=100 + index,
        )
        for index, (strategy_id, symbol) in enumerate(
            (strategy_id, symbol)
            for symbol in ("R_10", "R_25", "R_50", "R_75", "R_100")
            for strategy_id in DerivDigitStrategyId
        )
    )
    snapshot = replace(base, strategy_matrix=matrix)
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", lambda: snapshot)
    assert trader.evaluate_once() is True
    assert len(runtime.requests) == 1
    winner_events = tuple(
        item
        for item in runtime.event_sink.events
        if item.event_name == "digit_signal_arbitration_winner"
    )
    rejected_events = tuple(
        item
        for item in runtime.event_sink.events
        if item.event_name == "digit_signal_arbitration_rejected"
    )
    assert len(winner_events) == 1
    assert len(rejected_events) == 14
    assert ("entry_mode", "EXECUTABLE_SIGNAL") in winner_events[0].fields
    assert ("execution_environment", "DEMO") in winner_events[0].fields
    assert all(("entry_mode", "SHADOW_ONLY") in item.fields for item in rejected_events)
    assert all(("execution_environment", "DEMO") in item.fields for item in rejected_events)
    assert trader.evaluate_once() is False
    assert len(runtime.requests) == 1


def test_empty_selection_blocks_execution() -> None:
    runtime = _Runtime()
    runtime.risk_ledger.digit_config = DigitRiskConfig(
        selection_mode=StrategySelectionMode.MULTI,
        enabled_strategy_ids=frozenset(),
    )
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", _telemetry)
    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_NO_STRATEGY_SELECTED"
    assert runtime.requests == []


def test_disabled_strategy_still_emits_shadow_signal() -> None:
    runtime = _Runtime()
    runtime.risk_ledger.digit_config = DigitRiskConfig(
        enabled_strategy_ids=frozenset({"selective-differs-edge"})
    )
    snapshot = _telemetry()
    assert snapshot.synthetic_strategies[0].last_signal_epoch == 123
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", lambda: snapshot)
    assert trader.evaluate_once() is False
    assert runtime.requests == []
