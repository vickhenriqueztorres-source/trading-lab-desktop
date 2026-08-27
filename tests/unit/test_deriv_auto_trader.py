from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.core.deriv_auto_trader import DerivDigitAutoTrader
from apps.core.deriv_telemetry import DerivTelemetrySnapshot, DerivTelemetrySource
from apps.core.digit_risk_config import DigitRiskConfig
from packages.domain.models import Money, OrderRequest
from packages.market_data import DigitFrequencySnapshot
from packages.strategies.deriv_digits import (
    DerivDigitStrategyId,
    DigitAssetShadowProjection,
    DigitAssetShadowState,
    DigitStrategyProjection,
    ShadowSignalState,
)


class _Reader:
    def ui_order_summaries(self, limit: int) -> list[dict[str, object]]:
        assert limit == 100
        return []


class _RiskLedger:
    digit_config = DigitRiskConfig(auto_select_symbol=False)

    def digit_entry_stake(self, _health_gate: object) -> Money:
        return Money(self.digit_config.stake_minor_units, self.digit_config.currency)


class _Runtime:
    dispatcher_started = True
    health_gate = object()

    def __init__(self) -> None:
        self.reader = _Reader()
        self.risk_ledger = _RiskLedger()
        self.requests: list[OrderRequest] = []

    def submit(self, request: OrderRequest) -> None:
        self.requests.append(request)


def _telemetry() -> DerivTelemetrySnapshot:
    counts = (50,) * 10
    percentages = (Decimal("10"),) * 10
    signal = DigitStrategyProjection(
        strategy_id=DerivDigitStrategyId.TAIL_PROBABILITY_EDGE,
        display_name="Tail Probability Edge",
        markets="R_100 · 1 tick",
        lifecycle_status="PRACTICE_VALIDATION",
        signal_state=ShadowSignalState.SHADOW_SIGNAL,
        reason_code="TAIL_EDGE_SIGNAL",
        warmup_current=500,
        warmup_required=500,
        last_signal_epoch=123,
        last_signal_symbol="R_100",
        last_contract_type="DIGITOVER",
        last_direction="OVER",
        last_barrier=2,
        estimated_probability_pct=Decimal("75"),
        required_probability_pct=Decimal("72"),
        analysis_latency_microseconds=5,
    )
    return DerivTelemetrySnapshot(
        DerivTelemetrySource.DEMO_LIVE,
        "DEMO",
        True,
        None,
        None,
        None,
        DigitFrequencySnapshot("R_100", 500, counts, percentages, 0),
        (signal,),
    )


def test_auto_trader_submits_one_core_owned_digit_request_per_new_snapshot() -> None:
    runtime = _Runtime()
    trader = DerivDigitAutoTrader(
        runtime,  # type: ignore[arg-type]
        "DOT-DEMO",
        _telemetry,
        monotonic_clock=lambda: 10.0,
    )

    assert trader.evaluate_once() is True
    assert len(runtime.requests) == 1
    request = runtime.requests[0]
    assert request.prediction_digit == 2
    assert request.product == "DIGITOVER"
    assert request.strategy_id == "tail-probability-edge"
    assert trader.evaluate_once() is False
    assert trader.last_reason in {"BOT_ENTRY_THROTTLED", "BOT_WAITING_FOR_NEW_TICK"}


def test_auto_trader_obeys_central_bot_stop() -> None:
    runtime = _Runtime()
    runtime.dispatcher_started = False
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", _telemetry)  # type: ignore[arg-type]

    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_DISABLED_OR_HEALTH_BLOCKED"
    assert runtime.requests == []


def test_auto_trader_requires_explicit_operator_arming_even_if_dispatcher_is_open() -> None:
    runtime = _Runtime()
    trader = DerivDigitAutoTrader(
        runtime,  # type: ignore[arg-type]
        "DOT-DEMO",
        _telemetry,
        operator_armed=lambda: False,
    )

    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_OPERATOR_NOT_ARMED"
    assert runtime.requests == []


def test_auto_trader_wakes_immediately_for_tick_without_periodic_polling() -> None:
    runtime = _Runtime()
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", _telemetry)  # type: ignore[arg-type]
    trader.start()
    try:
        deadline = time.monotonic() + 0.25
        while not runtime.requests and time.monotonic() < deadline:
            trader.notify_tick()
            time.sleep(0.001)
        assert len(runtime.requests) == 1
        assert trader.latency_metrics["signal_to_analysis_microseconds"] < 50_000
    finally:
        trader.stop()


@pytest.mark.parametrize(
    "strategy_id,contract_type,direction,barrier",
    [
        (DerivDigitStrategyId.TAIL_PROBABILITY_EDGE, "DIGITUNDER", "UNDER", 7),
        (DerivDigitStrategyId.SELECTIVE_DIFFERS_EDGE, "DIGITDIFF", "DIFFERS", 4),
        (DerivDigitStrategyId.PARITY_REGIME_EDGE, "DIGITODD", "ODD", None),
    ],
)
def test_auto_trader_maps_each_strategy_signal_to_its_demo_contract(
    strategy_id: DerivDigitStrategyId,
    contract_type: str,
    direction: str,
    barrier: int | None,
) -> None:
    runtime = _Runtime()
    runtime.risk_ledger.digit_config = replace(
        runtime.risk_ledger.digit_config,
        active_strategy_id=strategy_id.value,
    )
    base = _telemetry()
    signal = replace(
        base.synthetic_strategies[0],
        strategy_id=strategy_id,
        last_contract_type=contract_type,
        last_direction=direction,
        last_barrier=barrier,
    )
    snapshot = replace(base, synthetic_strategies=(signal,))
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", lambda: snapshot)  # type: ignore[arg-type]

    assert trader.evaluate_once() is True
    assert runtime.requests[0].product == contract_type
    assert runtime.requests[0].prediction_digit == barrier
    assert runtime.requests[0].strategy_id == strategy_id.value


def test_auto_trader_rearm_requires_a_signal_after_the_pause_boundary() -> None:
    runtime = _Runtime()
    current = _telemetry()
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", lambda: current)  # type: ignore[arg-type]

    trader.begin_new_run()
    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_WAITING_FOR_NEW_TICK"
    current = replace(
        current,
        synthetic_strategies=(replace(current.synthetic_strategies[0], last_signal_epoch=124),),
    )
    assert trader.evaluate_once() is True
    assert len(runtime.requests) == 1


def test_auto_trader_selects_best_ranked_asset_in_demo_mode() -> None:
    runtime = _Runtime()
    runtime.risk_ledger.digit_config = DigitRiskConfig(auto_select_symbol=True)
    base = _telemetry()
    ranking = (
        DigitAssetShadowProjection(
            symbol="R_50",
            state=DigitAssetShadowState.CANDIDATE,
            reason_code="ASSET_SHADOW_CANDIDATE",
            warmup_current=500,
            warmup_required=500,
            selected=True,
            strategy_id=DerivDigitStrategyId.TAIL_PROBABILITY_EDGE,
            contract_type="DIGITOVER",
            barrier=2,
            estimated_probability_pct=Decimal("77.00"),
            required_probability_pct=Decimal("72.00"),
            conservative_margin_pct=Decimal("5.00"),
            last_signal_epoch=124,
        ),
        DigitAssetShadowProjection(
            symbol="R_25",
            state=DigitAssetShadowState.CANDIDATE,
            reason_code="ASSET_SHADOW_CANDIDATE",
            warmup_current=500,
            warmup_required=500,
            strategy_id=DerivDigitStrategyId.TAIL_PROBABILITY_EDGE,
            contract_type="DIGITOVER",
            barrier=2,
            estimated_probability_pct=Decimal("75.00"),
            required_probability_pct=Decimal("72.00"),
            conservative_margin_pct=Decimal("3.00"),
            last_signal_epoch=124,
        ),
    )
    snapshot = replace(base, asset_ranking=ranking)
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", lambda: snapshot)  # type: ignore[arg-type]

    assert trader.evaluate_once() is True
    assert runtime.requests[0].symbol == "R_50"


def test_auto_trader_abstains_from_strategy_with_negative_recent_net_result() -> None:
    class LosingReader(_Reader):
        def deriv_strategy_performance(
            self,
            strategy_id: str,
            *,
            symbol: str,
            limit: int,
        ) -> dict[str, object]:
            assert strategy_id == "tail-probability-edge"
            assert symbol == "R_100"
            assert limit == 30
            return {
                "settled_count": 10,
                "wins": 8,
                "losses": 2,
                "total_pnl_minor": -100,
                "avg_win_minor": 9.0,
                "avg_loss_minor": 100.0,
                "last_settled_at": datetime(2026, 8, 26, tzinfo=UTC).isoformat(),
            }

    runtime = _Runtime()
    runtime.reader = LosingReader()
    trader = DerivDigitAutoTrader(
        runtime,
        "DOT-DEMO",
        _telemetry,
        utc_clock=lambda: datetime(2026, 8, 26, tzinfo=UTC),
    )  # type: ignore[arg-type]

    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_PERFORMANCE_COOLDOWN"
    assert runtime.requests == []


def test_auto_trader_does_not_apply_r10_circuit_breaker_to_r50() -> None:
    class AssetScopedReader(_Reader):
        def deriv_strategy_performance(
            self,
            strategy_id: str,
            *,
            symbol: str,
            limit: int,
        ) -> dict[str, object]:
            assert strategy_id == "tail-probability-edge"
            assert limit == 30
            if symbol == "R_10":
                return {
                    "settled_count": 10,
                    "wins": 4,
                    "losses": 6,
                    "total_pnl_minor": -100,
                    "avg_win_minor": 40.0,
                    "avg_loss_minor": 100.0,
                    "last_settled_at": datetime.now(UTC).isoformat(),
                }
            return {
                "settled_count": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl_minor": 0,
                "avg_win_minor": None,
                "avg_loss_minor": None,
                "last_settled_at": None,
            }

    runtime = _Runtime()
    runtime.reader = AssetScopedReader()
    runtime.risk_ledger.digit_config = DigitRiskConfig(auto_select_symbol=True)
    base = _telemetry()
    ranking = (
        DigitAssetShadowProjection(
            symbol="R_10",
            state=DigitAssetShadowState.CANDIDATE,
            reason_code="ASSET_SHADOW_CANDIDATE",
            warmup_current=500,
            warmup_required=500,
            strategy_id=DerivDigitStrategyId.TAIL_PROBABILITY_EDGE,
            contract_type="DIGITOVER",
            barrier=2,
            estimated_probability_pct=Decimal("78.00"),
            required_probability_pct=Decimal("72.00"),
            conservative_margin_pct=Decimal("6.00"),
            last_signal_epoch=300,
        ),
        DigitAssetShadowProjection(
            symbol="R_50",
            state=DigitAssetShadowState.CANDIDATE,
            reason_code="ASSET_SHADOW_CANDIDATE",
            warmup_current=500,
            warmup_required=500,
            selected=True,
            strategy_id=DerivDigitStrategyId.TAIL_PROBABILITY_EDGE,
            contract_type="DIGITOVER",
            barrier=2,
            estimated_probability_pct=Decimal("76.00"),
            required_probability_pct=Decimal("72.00"),
            conservative_margin_pct=Decimal("4.00"),
            last_signal_epoch=300,
        ),
    )
    trader = DerivDigitAutoTrader(
        runtime,
        "DOT-DEMO",
        lambda: replace(base, asset_ranking=ranking),
    )  # type: ignore[arg-type]

    assert trader.evaluate_once() is True
    assert runtime.requests[0].symbol == "R_50"


def test_negative_performance_cooldown_reopens_a_bounded_probe_batch() -> None:
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)

    class RecoveringReader(_Reader):
        calls = 0

        def deriv_strategy_performance(
            self,
            strategy_id: str,
            *,
            symbol: str,
            limit: int,
        ) -> dict[str, object]:
            assert strategy_id == "tail-probability-edge"
            assert symbol == "R_100"
            assert limit == 30
            self.calls += 1
            last_settled = now - timedelta(minutes=11) if self.calls == 1 else now
            return {
                "settled_count": 10,
                "wins": 8,
                "losses": 2,
                "total_pnl_minor": -100,
                "avg_win_minor": 100.0,
                "avg_loss_minor": 100.0,
                "last_settled_at": last_settled.isoformat(),
            }

    runtime = _Runtime()
    runtime.reader = RecoveringReader()
    current = _telemetry()
    trader = DerivDigitAutoTrader(
        runtime,
        "DOT-DEMO",
        lambda: current,
        utc_clock=lambda: now,
    )  # type: ignore[arg-type]

    for epoch in range(123, 133):
        current = replace(
            current,
            synthetic_strategies=(
                replace(current.synthetic_strategies[0], last_signal_epoch=epoch),
            ),
        )
        assert trader.evaluate_once() is True
    current = replace(
        current,
        synthetic_strategies=(replace(current.synthetic_strategies[0], last_signal_epoch=133),),
    )
    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_PERFORMANCE_COOLDOWN"
    assert len(runtime.requests) == 10
