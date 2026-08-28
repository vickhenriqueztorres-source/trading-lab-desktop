from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.core.deriv_auto_trader import DerivDigitAutoTrader
from apps.core.deriv_telemetry import DerivTelemetrySnapshot, DerivTelemetrySource
from apps.core.digit_risk_config import DigitRiskConfig
from packages.domain.models import (
    Broker,
    BrokerOrderEvent,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderRequest,
    OrderState,
)
from packages.market_data import DigitFrequencySnapshot
from packages.observability.events import InMemoryEventSink
from packages.persistence.writer import BrokerEventApplyResult, BrokerEventApplyStatus
from packages.strategies.deriv_digits import (
    DerivDigitStrategyId,
    DigitAssetShadowProjection,
    DigitAssetShadowState,
    DigitStrategyProjection,
    ShadowSignalState,
)


class _Reader:
    def list_nonterminal_orders(self) -> list[dict[str, object]]:
        return []

    def deriv_recent_strategy_settlements(self, *, limit_per_scope: int) -> list[dict[str, object]]:
        assert limit_per_scope == 30
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
        self.event_sink = InMemoryEventSink()

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


def test_expired_digit_cooldown_is_refreshed_before_global_gate_check() -> None:
    class GateState:
        is_open = False

    class Gate:
        state = GateState()

    class RefreshingRiskLedger(_RiskLedger):
        refresh_calls = 0

        def refresh_digit_health_gate(self, gate: Gate) -> None:
            self.refresh_calls += 1
            gate.state.is_open = True

    runtime = _Runtime()
    runtime.health_gate = Gate()
    runtime.risk_ledger = RefreshingRiskLedger()
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", _telemetry)  # type: ignore[arg-type]

    assert trader.evaluate_once() is True
    assert runtime.risk_ledger.refresh_calls == 1
    assert len(runtime.requests) == 1


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


def test_begin_new_run_semantics_unchanged() -> None:
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
        def deriv_recent_strategy_settlements(
            self, *, limit_per_scope: int
        ) -> list[dict[str, object]]:
            return _performance_rows(
                [9] * 8 + [-86] * 2,
                datetime(2026, 8, 26, tzinfo=UTC),
            )

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


def test_new_test_session_excludes_old_performance_from_runtime_cache() -> None:
    boundary = datetime(2026, 8, 27, tzinfo=UTC)

    class SessionReader(_Reader):
        received_boundary: datetime | None = None

        def digit_test_session_started_at(self) -> datetime:
            return boundary

        def deriv_recent_strategy_settlements(
            self,
            *,
            limit_per_scope: int,
            since_utc: datetime | None = None,
        ) -> list[dict[str, object]]:
            assert limit_per_scope == 30
            self.received_boundary = since_utc
            return []

    runtime = _Runtime()
    reader = SessionReader()
    runtime.reader = reader
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", _telemetry)  # type: ignore[arg-type]

    assert reader.received_boundary == boundary
    assert trader.evaluate_once() is True
    assert len(runtime.requests) == 1


def test_auto_trader_does_not_apply_r10_circuit_breaker_to_r50() -> None:
    class AssetScopedReader(_Reader):
        def deriv_recent_strategy_settlements(
            self, *, limit_per_scope: int
        ) -> list[dict[str, object]]:
            return _performance_rows(
                [40] * 4 + [-50] * 6,
                datetime.now(UTC),
                symbol="R_10",
            )

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

        def deriv_recent_strategy_settlements(
            self, *, limit_per_scope: int
        ) -> list[dict[str, object]]:
            self.calls += 1
            last_settled = now - timedelta(minutes=11) if self.calls == 1 else now
            return _performance_rows([100] * 8 + [-450] * 2, last_settled)

    runtime = _Runtime()
    runtime.reader = RecoveringReader()
    current = _telemetry()
    current = replace(
        current,
        synthetic_strategies=(
            replace(current.synthetic_strategies[0], estimated_probability_pct=Decimal("95")),
        ),
    )
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
    trader.reload_runtime_caches()
    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_PERFORMANCE_COOLDOWN"
    assert len(runtime.requests) == 10


def _performance_rows(
    pnl_values: list[int],
    settled_at: datetime,
    *,
    symbol: str = "R_100",
) -> list[dict[str, object]]:
    return [
        {
            "order_id": f"order-{index}",
            "strategy_id": "tail-probability-edge",
            "symbol": symbol,
            "realized_pnl_minor": pnl,
            "settled_at": (settled_at - timedelta(seconds=len(pnl_values) - index)).isoformat(),
        }
        for index, pnl in enumerate(pnl_values)
    ]


def _broker_event(
    order_id: str,
    status: ExternalOrderStatus,
    pnl: int | None = None,
) -> BrokerOrderEvent:
    now = datetime(2026, 8, 27, tzinfo=UTC)
    payload: dict[str, object] = {
        "event_id": f"event-{status.value}",
        "event_version": 1,
        "broker": Broker.DERIV.value,
        "account_id": "DOT-DEMO",
        "client_order_ref": order_id,
        "broker_order_id": "broker-order",
        "correlation_id": "correlation",
        "external_sequence": 1,
        "external_status": status.value,
        "occurred_at": now.isoformat(),
        "observed_at": now.isoformat(),
        "product": "DIGITOVER",
        "symbol": "R_100",
        "direction": Direction.CALL.value,
        "amount_minor": 100,
        "currency": "USD",
        "result_minor": pnl,
        "result_currency": "USD" if pnl is not None else None,
    }
    payload["evidence_hash"] = BrokerOrderEvent.evidence_hash_for_payload(payload)
    return BrokerOrderEvent.from_payload(payload)


def test_evaluate_once_does_not_read_database() -> None:
    class SpyReader(_Reader):
        calls = 0

        def list_nonterminal_orders(self) -> list[dict[str, object]]:
            self.calls += 1
            return []

        def deriv_recent_strategy_settlements(
            self, *, limit_per_scope: int
        ) -> list[dict[str, object]]:
            self.calls += 1
            return []

    runtime = _Runtime()
    reader = SpyReader()
    runtime.reader = reader
    snapshot = replace(_telemetry(), synthetic_strategies=())
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", lambda: snapshot)  # type: ignore[arg-type]
    startup_calls = reader.calls

    for _ in range(1_000):
        assert trader.evaluate_once() is False

    assert reader.calls == startup_calls
    assert runtime.requests == []


def test_inflight_cache_is_seeded_from_database_on_startup() -> None:
    class OpenOrderReader(_Reader):
        def list_nonterminal_orders(self) -> list[dict[str, object]]:
            return [
                {
                    "order_id": "open-1",
                    "broker": "DERIV",
                    "state": "OPEN",
                    "strategy_id": "tail-probability-edge",
                    "symbol": "R_100",
                }
            ]

    runtime = _Runtime()
    runtime.reader = OpenOrderReader()
    telemetry_calls = 0

    def telemetry() -> DerivTelemetrySnapshot:
        nonlocal telemetry_calls
        telemetry_calls += 1
        return _telemetry()

    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", telemetry)  # type: ignore[arg-type]

    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_ORDER_IN_FLIGHT"
    assert telemetry_calls == 0


def test_cheap_checks_short_circuit_before_telemetry() -> None:
    class OpenOrderReader(_Reader):
        def list_nonterminal_orders(self) -> list[dict[str, object]]:
            return [
                {
                    "order_id": "open-1",
                    "broker": "DERIV",
                    "state": "OPEN",
                    "strategy_id": "tail-probability-edge",
                    "symbol": "R_100",
                }
            ]

    runtime = _Runtime()
    runtime.reader = OpenOrderReader()
    telemetry_calls = 0

    def telemetry() -> DerivTelemetrySnapshot:
        nonlocal telemetry_calls
        telemetry_calls += 1
        return _telemetry()

    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", telemetry)  # type: ignore[arg-type]

    assert trader.evaluate_once() is False
    assert telemetry_calls == 0


def test_order_events_update_inflight_cache_without_reader_query() -> None:
    runtime = _Runtime()
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", _telemetry)  # type: ignore[arg-type]

    trader.notify_order_event(
        _broker_event("order-1", ExternalOrderStatus.OPEN),
        BrokerEventApplyResult(BrokerEventApplyStatus.APPLIED, OrderState.OPEN, None),
    )
    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_ORDER_IN_FLIGHT"

    trader.notify_order_event(
        _broker_event("order-1", ExternalOrderStatus.SETTLED, 80),
        BrokerEventApplyResult(BrokerEventApplyStatus.APPLIED, OrderState.SETTLED, None),
    )
    assert trader.evaluate_once() is True


def test_inflight_cache_reloads_after_reconciliation() -> None:
    class MutableReader(_Reader):
        rows: list[dict[str, object]] = []

        def list_nonterminal_orders(self) -> list[dict[str, object]]:
            return self.rows

    runtime = _Runtime()
    reader = MutableReader()
    runtime.reader = reader
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", _telemetry)  # type: ignore[arg-type]

    reader.rows = [
        {
            "order_id": "open-2",
            "broker": "DERIV",
            "state": "OPEN",
            "strategy_id": "tail-probability-edge",
            "symbol": "R_100",
        }
    ]
    trader.reload_runtime_caches()

    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_ORDER_IN_FLIGHT"

    reader.rows = []
    trader.reload_runtime_caches()

    assert trader.evaluate_once() is True


def test_synchronous_rejected_submit_does_not_leave_inflight_cache() -> None:
    class TerminalOrderReader(_Reader):
        def one(self, table: str, key_name: str, key_value: str) -> dict[str, object] | None:
            if (table, key_name, key_value) == ("orders", "order_id", "rejected-order"):
                return {"order_id": "rejected-order", "state": "REJECTED"}
            assert (table, key_name, key_value) == (
                "outbox_messages",
                "message_id",
                "rejected-message",
            )
            return {
                "message_id": "rejected-message",
                "state": "DISPATCHED",
                "state_reason": "DERIV_INVALID_REQUEST",
            }

    class RejectedRuntime(_Runtime):
        def submit(self, request: OrderRequest) -> object:
            self.requests.append(request)
            return SimpleNamespace(
                order_id="rejected-order",
                message_id="rejected-message",
            )

    runtime = RejectedRuntime()
    runtime.reader = TerminalOrderReader()
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", _telemetry)  # type: ignore[arg-type]

    assert trader.evaluate_once() is False

    assert len(runtime.requests) == 1
    assert trader._has_open_deriv_order() is False
    assert trader.last_reason == "BOT_ENTRY_REJECTED_DERIV_INVALID_REQUEST"
    assert any(
        item.event_name == "autotrader_inflight_cache_divergence"
        and ("cached_count", 1) in item.fields
        and ("persisted_count", 0) in item.fields
        for item in runtime.event_sink.events
    )
    assert any(
        item.event_name == "autotrader_order_rejected"
        and item.reason_code == "DERIV_INVALID_REQUEST"
        and ("amount_minor_units", 100) in item.fields
        for item in runtime.event_sink.events
    )


def test_uninitialized_cache_blocks_entry() -> None:
    class MutableReader(_Reader):
        fail = True

        def list_nonterminal_orders(self) -> list[dict[str, object]]:
            if self.fail:
                raise RuntimeError("unavailable")
            return []

    runtime = _Runtime()
    reader = MutableReader()
    runtime.reader = reader
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", _telemetry)  # type: ignore[arg-type]

    assert trader.evaluate_once() is False
    assert trader.last_reason == "BOT_ORDER_STATE_UNAVAILABLE"
    assert runtime.requests == []


def test_cache_divergence_is_reported() -> None:
    class MutableReader(_Reader):
        rows: list[dict[str, object]] = []

        def list_nonterminal_orders(self) -> list[dict[str, object]]:
            return self.rows

    runtime = _Runtime()
    reader = MutableReader()
    runtime.reader = reader
    trader = DerivDigitAutoTrader(runtime, "DOT-DEMO", _telemetry)  # type: ignore[arg-type]

    reader.rows = [
        {
            "order_id": "open-2",
            "broker": "DERIV",
            "state": "OPEN",
            "strategy_id": "tail-probability-edge",
            "symbol": "R_100",
        }
    ]
    trader.reload_runtime_caches()
    assert any(
        item.event_name == "autotrader_inflight_cache_divergence"
        for item in runtime.event_sink.events
    )


def test_tick_coalescing_produces_single_evaluation() -> None:
    runtime = _Runtime()
    clock = [0.0]
    waits: list[float] = []

    def waiter(seconds: float) -> bool:
        waits.append(seconds)
        clock[0] += seconds
        return False

    trader = DerivDigitAutoTrader(
        runtime,  # type: ignore[arg-type]
        "DOT-DEMO",
        _telemetry,
        monotonic_clock=lambda: clock[0],
        interval_waiter=waiter,
    )
    evaluations = 0

    def evaluate() -> bool:
        nonlocal evaluations
        evaluations += 1
        return False

    trader.evaluate_once = evaluate  # type: ignore[method-assign]
    for _ in range(50):
        trader.notify_tick()
    assert trader._process_pending_once() is True
    assert trader._process_pending_once() is False
    assert evaluations == 1

    trader.notify_tick()
    assert trader._process_pending_once() is True
    assert evaluations == 2
    assert waits == [pytest.approx(0.25)]


def test_min_evaluation_interval_does_not_delay_real_decisions() -> None:
    runtime = _Runtime()
    clock = [0.0]
    waits: list[float] = []
    trader = DerivDigitAutoTrader(
        runtime,  # type: ignore[arg-type]
        "DOT-DEMO",
        _telemetry,
        monotonic_clock=lambda: clock[0],
        interval_waiter=lambda seconds: bool(waits.append(seconds)),
    )
    evaluations = 0

    def evaluate() -> bool:
        nonlocal evaluations
        evaluations += 1
        return False

    trader.evaluate_once = evaluate  # type: ignore[method-assign]

    for second in (0.0, 2.0, 4.0):
        clock[0] = second
        trader.notify_tick()
        assert trader._process_pending_once() is True

    assert evaluations == 3
    assert waits == []


def test_last_reason_is_exposed_with_waiting_duration() -> None:
    runtime = _Runtime()
    clock = [10.0]
    trader = DerivDigitAutoTrader(
        runtime,  # type: ignore[arg-type]
        "DOT-DEMO",
        _telemetry,
        monotonic_clock=lambda: clock[0],
    )
    trader.begin_new_run()
    clock[0] = 17.9

    status = trader.waiting_status
    assert status.reason_code == "BOT_WAITING_FOR_NEW_TICK"
    assert status.waiting_since_seconds == 7
    assert status.armed_epoch == 123
    assert status.rearm_notice is True
    assert "descartou sinais anteriores" in status.description


def test_rearm_resets_waiting_and_is_reported() -> None:
    runtime = _Runtime()
    clock = [10.0]
    trader = DerivDigitAutoTrader(
        runtime,  # type: ignore[arg-type]
        "DOT-DEMO",
        _telemetry,
        monotonic_clock=lambda: clock[0],
    )

    trader.begin_new_run()
    clock[0] = 30.0
    assert trader.waiting_status.waiting_since_seconds == 20

    trader.begin_new_run()

    status = trader.waiting_status
    assert status.reason_code == "BOT_WAITING_FOR_NEW_TICK"
    assert status.waiting_since_seconds == 0
    assert status.rearm_notice is True


def test_no_order_is_ever_sent_by_this_path() -> None:
    runtime = _Runtime()
    trader = DerivDigitAutoTrader(
        runtime,  # type: ignore[arg-type]
        "DOT-DEMO",
        _telemetry,
        operator_armed=lambda: False,
    )

    assert trader.evaluate_once() is False
    trader.notify_tick()
    trader._process_pending_once()

    assert runtime.requests == []


def test_ten_thousand_ticks_do_not_read_database_or_submit() -> None:
    runtime = _Runtime()
    trader = DerivDigitAutoTrader(
        runtime,  # type: ignore[arg-type]
        "DOT-DEMO",
        _telemetry,
        operator_armed=lambda: False,
    )
    started = time.perf_counter()
    for _ in range(10_000):
        trader.notify_tick()
    elapsed = time.perf_counter() - started
    trader._process_pending_once()

    assert elapsed < 1.0
    assert runtime.requests == []
