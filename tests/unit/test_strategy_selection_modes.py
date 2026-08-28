from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from apps.core.deriv_auto_trader import DerivDigitAutoTrader
from apps.core.deriv_telemetry import DerivTelemetrySource
from apps.core.digit_risk_config import DigitRiskConfig, StrategySelectionMode
from packages.strategies.deriv_digits import DerivDigitStrategyId
from tests.unit.test_deriv_auto_trader import _Runtime, _telemetry


def _signals():
    base = _telemetry()
    return base, tuple(
        replace(
            base.synthetic_strategies[0],
            strategy_id=strategy,
            last_contract_type=contract,
            last_direction=direction,
            last_barrier=barrier,
            last_signal_epoch=epoch,
            estimated_probability_pct=Decimal("95"),
        )
        for strategy, contract, direction, barrier, epoch in (
            (DerivDigitStrategyId.TAIL_PROBABILITY_EDGE, "DIGITOVER", "OVER", 2, 501),
            (DerivDigitStrategyId.SELECTIVE_DIFFERS_EDGE, "DIGITDIFF", "DIFFERS", 4, 502),
            (DerivDigitStrategyId.PARITY_REGIME_EDGE, "DIGITODD", "ODD", None, 503),
        )
    )


def test_single_mode_executes_only_active_strategy() -> None:
    base, signals = _signals()
    runtime = _Runtime()
    runtime.risk_ledger.digit_config = replace(
        DigitRiskConfig(),
        auto_select_symbol=False,
        active_strategy_id=DerivDigitStrategyId.SELECTIVE_DIFFERS_EDGE.value,
        selection_mode=StrategySelectionMode.SINGLE,
    )
    trader = DerivDigitAutoTrader(
        runtime, "DEMO", lambda: replace(base, synthetic_strategies=signals)
    )  # type: ignore[arg-type]
    assert trader.evaluate_once() is True
    assert runtime.requests[0].strategy_id == DerivDigitStrategyId.SELECTIVE_DIFFERS_EDGE.value


def test_active_strategy_id_governs_in_single_mode() -> None:
    base, signals = _signals()
    runtime = _Runtime()
    config = replace(
        DigitRiskConfig(),
        auto_select_symbol=False,
        active_strategy_id=DerivDigitStrategyId.TAIL_PROBABILITY_EDGE.value,
        selection_mode=StrategySelectionMode.SINGLE,
    )
    runtime.risk_ledger.digit_config = config
    current = [replace(base, synthetic_strategies=signals)]
    trader = DerivDigitAutoTrader(runtime, "DEMO", lambda: current[0])  # type: ignore[arg-type]
    assert trader.evaluate_once() is True
    assert runtime.requests[-1].strategy_id == DerivDigitStrategyId.TAIL_PROBABILITY_EDGE.value
    runtime.requests.clear()
    runtime.risk_ledger.digit_config = replace(
        config,
        active_strategy_id=DerivDigitStrategyId.PARITY_REGIME_EDGE.value,
    )
    current[0] = replace(base, synthetic_strategies=signals)
    assert trader.evaluate_once() is True
    assert runtime.requests[-1].strategy_id == DerivDigitStrategyId.PARITY_REGIME_EDGE.value


def test_multi_mode_uses_enabled_set() -> None:
    base, signals = _signals()
    runtime = _Runtime()
    runtime.risk_ledger.digit_config = replace(
        DigitRiskConfig(),
        auto_select_symbol=False,
        selection_mode=StrategySelectionMode.MULTI,
        enabled_strategy_ids=frozenset({"parity-regime-edge"}),
    )
    trader = DerivDigitAutoTrader(
        runtime, "DEMO", lambda: replace(base, synthetic_strategies=signals)
    )  # type: ignore[arg-type]
    assert trader.evaluate_once() is True
    assert runtime.requests[0].strategy_id == "parity-regime-edge"


def test_stress_default_is_off_and_requires_demo() -> None:
    assert DigitRiskConfig().selection_mode is StrategySelectionMode.SINGLE
    base, signals = _signals()
    runtime = _Runtime()
    runtime.risk_ledger.digit_config = replace(
        DigitRiskConfig(), selection_mode=StrategySelectionMode.STRESS
    )
    real = replace(base, source=DerivTelemetrySource.REAL_LIVE)
    trader = DerivDigitAutoTrader(
        runtime, "REAL", lambda: replace(real, synthetic_strategies=signals)
    )  # type: ignore[arg-type]
    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_STRESS_MODE_REQUIRES_DEMO"


def test_empty_selection_blocks_execution_with_reason() -> None:
    runtime = _Runtime()
    runtime.risk_ledger.digit_config = replace(
        DigitRiskConfig(),
        selection_mode=StrategySelectionMode.MULTI,
        enabled_strategy_ids=frozenset(),
    )
    trader = DerivDigitAutoTrader(runtime, "DEMO", _telemetry)  # type: ignore[arg-type]
    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_NO_STRATEGY_SELECTED"


def test_orphan_active_strategy_id_fails_closed() -> None:
    runtime = _Runtime()
    runtime.risk_ledger.digit_config = replace(
        DigitRiskConfig(), active_strategy_id="orphan-strategy"
    )
    trader = DerivDigitAutoTrader(runtime, "DEMO", _telemetry)  # type: ignore[arg-type]
    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_NO_STRATEGY_SELECTED"
