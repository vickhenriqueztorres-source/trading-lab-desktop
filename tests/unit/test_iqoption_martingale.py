from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.core.iqoption_auto_trader import IqOptionAutoTrader
from apps.core.iqoption_failures import IQFailurePolicy
from apps.core.iqoption_martingale import IqOptionCandleOutcome, IqOptionMartingaleCycle
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from packages.domain.market import MarketCandle
from packages.domain.models import (
    Broker,
    BrokerOrderEvent,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderRequest,
    OrderState,
)
from packages.persistence.writer import BrokerEventApplyResult, BrokerEventApplyStatus
from packages.protocol.ui_messages import UiIqOptionRiskConfig


class _Reader:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self.row = row

    def one(self, _table: str, _key: str, _value: str) -> dict[str, str]:
        if self.row is not None:
            return self.row  # type: ignore[return-value]
        return {"state": OrderState.ACCEPTED.value}

    @staticmethod
    def outbox_for_intent(_intent_id: str) -> None:
        return None

    @staticmethod
    def list_nonterminal_orders() -> list[dict[str, str]]:
        return []


class _Runtime:
    def __init__(self) -> None:
        self.reader = _Reader()
        self.requests: list[OrderRequest] = []
        self.events: list[tuple[str, dict[str, object]]] = []
        self.event_sink = SimpleNamespace(
            emit=lambda name, **fields: self.events.append((name, fields))
        )

    def submit(self, request: OrderRequest) -> SimpleNamespace:
        self.requests.append(request)
        return SimpleNamespace(order_id=f"order-{len(self.requests)}", intent_id="intent")


class _StateWriter:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def load_iqoption_execution_state(self) -> dict[str, object]:
        return dict(self.payload)

    def save_iqoption_execution_state(self, payload: dict[str, object]) -> None:
        self.payload = payload


class _Client:
    @staticmethod
    def iqoption_binary_payout(_symbol: str) -> Decimal:
        return Decimal("0.80")


def _config(*, steps: int = 2) -> IqOptionRiskConfig:
    return IqOptionRiskConfig(
        martingale_enabled=True,
        martingale_max_steps=steps,
        martingale_multiplier_basis_points=20_000,
        martingale_max_stake_minor_units=400,
    )


def _cycle(
    config: IqOptionRiskConfig,
    target: datetime = datetime(2026, 9, 10, 12, 1, tzinfo=UTC),
) -> IqOptionMartingaleCycle:
    return IqOptionMartingaleCycle.start(
        config=config,
        strategy_id=config.strategy_id,
        symbol=config.symbol,
        direction=Direction.CALL,
        order_id="order-base",
        target_candle_close_utc=target,
    )


def _candle(target: datetime, *, outcome: IqOptionCandleOutcome) -> MarketCandle:
    close = {
        IqOptionCandleOutcome.WIN: Decimal("1.1010"),
        IqOptionCandleOutcome.LOSS: Decimal("1.0990"),
        IqOptionCandleOutcome.TIE: Decimal("1.1000"),
    }[outcome]
    return MarketCandle(
        broker=Broker.IQ_OPTION,
        broker_symbol="EURUSD-OTC",
        timeframe_seconds=60,
        open_time=target - timedelta(minutes=1),
        close_time=target,
        open=Decimal("1.1000"),
        high=Decimal("1.1020"),
        low=Decimal("1.0980"),
        close=close,
        is_closed=True,
    )


def _settlement_event(
    *, order_id: str, event_id: str, result_minor: int, occurred_at: datetime
) -> BrokerOrderEvent:
    payload: dict[str, object] = {
        "event_id": event_id,
        "event_version": 1,
        "broker": Broker.IQ_OPTION.value,
        "account_id": "PRACTICE",
        "client_order_ref": order_id,
        "broker_order_id": f"broker-{order_id}",
        "correlation_id": f"correlation-{order_id}",
        "external_sequence": 1,
        "external_status": ExternalOrderStatus.SETTLED.value,
        "occurred_at": occurred_at.isoformat(),
        "observed_at": occurred_at.isoformat(),
        "product": "BINARY_OPTION",
        "symbol": "EURUSD-OTC",
        "direction": Direction.CALL.value,
        "amount_minor": Money(100, "USD").minor_units,
        "currency": "USD",
        "result_minor": result_minor,
        "result_currency": "USD",
    }
    payload["evidence_hash"] = BrokerOrderEvent.evidence_hash_for_payload(payload)
    return BrokerOrderEvent.from_payload(payload)


def test_g2_progression_is_bounded_and_resets_after_the_last_result() -> None:
    config = _config(steps=2)
    cycle = _cycle(config)

    g1 = cycle.after_candle_close(
        _candle(cycle.target_candle_close_utc, outcome=IqOptionCandleOutcome.LOSS)
    )
    assert g1 is not None
    assert g1.step == 1
    assert g1.expected_stake_minor_units(config) == 200
    assert g1.recovery_not_before_utc == datetime(2026, 9, 10, 12, 1, tzinfo=UTC)

    g1_bound = g1.bind_recovery_order(
        "order-g1",
        stake_minor_units=200,
        target_candle_close_utc=datetime(2026, 9, 10, 12, 2, tzinfo=UTC),
    )
    g2 = g1_bound.after_candle_close(
        _candle(g1_bound.target_candle_close_utc, outcome=IqOptionCandleOutcome.LOSS)
    )
    assert g2 is not None
    assert g2.step == 2
    assert g2.cumulative_loss_minor_units == 300
    assert g2.expected_stake_minor_units(config) == 400

    g2_bound = g2.bind_recovery_order(
        "order-g2",
        stake_minor_units=400,
        target_candle_close_utc=datetime(2026, 9, 10, 12, 3, tzinfo=UTC),
    )
    finished = g2_bound.after_candle_close(
        _candle(g2_bound.target_candle_close_utc, outcome=IqOptionCandleOutcome.LOSS)
    )
    assert finished is None


def test_win_tie_and_duplicate_candle_evidence_never_create_extra_recovery() -> None:
    config = _config(steps=1)
    cycle = _cycle(config)

    assert (
        cycle.after_candle_close(
            _candle(cycle.target_candle_close_utc, outcome=IqOptionCandleOutcome.WIN)
        )
        is None
    )
    assert (
        cycle.after_candle_close(
            _candle(cycle.target_candle_close_utc, outcome=IqOptionCandleOutcome.TIE)
        )
        is None
    )
    pending = cycle.after_candle_close(
        _candle(cycle.target_candle_close_utc, outcome=IqOptionCandleOutcome.LOSS)
    )
    assert pending is not None
    assert (
        pending.after_candle_close(
            _candle(cycle.target_candle_close_utc, outcome=IqOptionCandleOutcome.LOSS)
        )
        == pending
    )


@pytest.mark.parametrize(
    ("direction", "candle_outcome", "expected"),
    [
        (Direction.CALL, IqOptionCandleOutcome.WIN, IqOptionCandleOutcome.WIN),
        (Direction.CALL, IqOptionCandleOutcome.LOSS, IqOptionCandleOutcome.LOSS),
        (Direction.PUT, IqOptionCandleOutcome.WIN, IqOptionCandleOutcome.LOSS),
        (Direction.PUT, IqOptionCandleOutcome.LOSS, IqOptionCandleOutcome.WIN),
        (Direction.PUT, IqOptionCandleOutcome.TIE, IqOptionCandleOutcome.TIE),
    ],
)
def test_candle_color_rule_is_explicit_for_call_put_and_tie(
    direction: Direction,
    candle_outcome: IqOptionCandleOutcome,
    expected: IqOptionCandleOutcome,
) -> None:
    candle = _candle(datetime(2026, 9, 10, 12, 1, tzinfo=UTC), outcome=candle_outcome)

    assert IqOptionMartingaleCycle.outcome_for_candle(direction, candle) is expected


def test_cycle_payload_round_trip_preserves_the_pinned_iq_scope() -> None:
    config = _config(steps=2)
    base = _cycle(config)
    cycle = base.after_candle_close(
        _candle(base.target_candle_close_utc, outcome=IqOptionCandleOutcome.LOSS)
    )
    assert cycle is not None

    restored = IqOptionMartingaleCycle.from_payload(cycle.to_payload())

    assert restored == cycle
    assert restored.strategy_id == "iqoption-rsi-demo"
    assert restored.symbol == "EURUSD-OTC"
    assert restored.direction is Direction.CALL


def test_recovery_uses_same_direction_and_passes_through_core_submission() -> None:
    config = _config(steps=1)
    runtime = _Runtime()
    now = datetime(2026, 9, 10, 12, 1, 5, tzinfo=UTC)
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=_Client()),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: config,
        operator_armed=lambda: True,
        utc_clock=lambda: now,
        monotonic=lambda: 100.0,
    )
    base = _cycle(config)
    pending = base.after_candle_close(
        _candle(base.target_candle_close_utc, outcome=IqOptionCandleOutcome.LOSS)
    )
    assert pending is not None
    trader._martingale_cycle = pending

    handled = trader._handle_martingale_cycle(
        supervisor=SimpleNamespace(client=_Client()),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
        risk_config=config,
        now_utc=now,
    )

    assert handled is True
    assert len(runtime.requests) == 1
    request = runtime.requests[0]
    assert request.amount.minor_units == 200
    assert request.direction is Direction.CALL
    assert request.strategy_id == config.strategy_id
    assert trader._martingale_cycle is not None
    assert trader._martingale_cycle.recovery_pending is False


def test_payout_finishing_after_recovery_deadline_never_moves_expiry() -> None:
    config = _config(steps=1)
    runtime = _Runtime()
    now = [datetime(2026, 9, 10, 12, 1, 18, tzinfo=UTC)]

    class SlowPayoutClient:
        @staticmethod
        def iqoption_binary_payout(_symbol: str) -> Decimal:
            now[0] += timedelta(seconds=3)
            return Decimal("0.80")

    client = SlowPayoutClient()
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: config,
        operator_armed=lambda: True,
        utc_clock=lambda: now[0],
        monotonic=lambda: 100.0,
    )
    base = _cycle(config)
    pending = base.after_candle_close(
        _candle(base.target_candle_close_utc, outcome=IqOptionCandleOutcome.LOSS)
    )
    assert pending is not None
    trader._martingale_cycle = pending

    handled = trader._handle_martingale_cycle(
        supervisor=SimpleNamespace(client=client),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
        risk_config=config,
        now_utc=now[0],
    )

    assert handled is True
    assert runtime.requests == []
    assert trader._martingale_cycle is None
    assert trader.status_reason == "IQOPTION_MARTINGALE_ENTRY_WINDOW_MISSED"


def test_financial_loss_does_not_drive_martingale_progression() -> None:
    config = _config(steps=2)
    occurred = datetime(2026, 9, 10, 12, 0, 5, tzinfo=UTC)
    runtime = _Runtime()
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: None,
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: config,
        operator_armed=lambda: True,
        utc_clock=lambda: occurred,
        monotonic=lambda: 10.0,
    )
    trader._martingale_cycle = _cycle(config)
    event = _settlement_event(
        order_id="order-base",
        event_id="settlement-0",
        result_minor=-100,
        occurred_at=occurred,
    )

    trader.notify_order_event(
        event,
        BrokerEventApplyResult(BrokerEventApplyStatus.APPLIED, OrderState.SETTLED, None),
    )
    unchanged = trader._martingale_cycle
    assert unchanged is not None
    assert unchanged.step == 0
    assert unchanged.technical_outcome is IqOptionCandleOutcome.PENDING

    trader.notify_order_event(
        event,
        BrokerEventApplyResult(BrokerEventApplyStatus.DUPLICATE, OrderState.SETTLED, None),
    )
    assert trader._martingale_cycle == unchanged


def test_disarm_cancels_only_an_unsent_recovery() -> None:
    config = _config(steps=1)
    occurred = datetime(2026, 9, 10, 12, 1, 5, tzinfo=UTC)
    base = _cycle(config)
    pending = base.after_candle_close(
        _candle(base.target_candle_close_utc, outcome=IqOptionCandleOutcome.LOSS)
    )
    assert pending is not None
    writer = _StateWriter({})
    runtime = _Runtime()
    runtime.writer = writer  # type: ignore[attr-defined]
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: None,
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: config,
        operator_armed=lambda: False,
        utc_clock=lambda: occurred,
        monotonic=lambda: 0.0,
    )
    trader._martingale_cycle = pending

    trader.stop()

    assert trader._martingale_cycle is None
    assert writer.payload["martingale_cycle"] is None


def test_config_rejects_a_sequence_larger_than_the_daily_stop() -> None:
    with pytest.raises(ValueError, match="IQOPTION_MARTINGALE_STOP_LOSS_TOO_LOW"):
        IqOptionRiskConfig(
            daily_stop_loss_minor_units=600,
            martingale_enabled=True,
            martingale_max_steps=2,
            martingale_max_stake_minor_units=400,
        )


def test_restart_does_not_derive_g1_from_financial_settlement() -> None:
    config = _config(steps=2)
    occurred = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    cycle = _cycle(config)
    payload: dict[str, object] = {
        "version": 3,
        "signals": {},
        "policy": IQFailurePolicy().dump(0.0, occurred),
        "pending": None,
        "martingale_cycle": cycle.to_payload(),
    }
    runtime = _Runtime()
    runtime.writer = _StateWriter(payload)  # type: ignore[attr-defined]
    runtime.reader = _Reader(
        {
            "state": OrderState.SETTLED.value,
            "realized_pnl_minor": -100,
            "last_broker_event_id": "settlement-after-crash",
            "updated_at": occurred.isoformat(),
        }
    )
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: None,
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: config,
        operator_armed=lambda: True,
        utc_clock=lambda: occurred + timedelta(minutes=1),
        monotonic=lambda: 0.0,
    )

    trader._restore_execution_state(runtime)  # type: ignore[arg-type]

    assert trader._martingale_cycle is not None
    assert trader._martingale_cycle.step == 0
    assert trader._martingale_cycle.recovery_pending is False
    assert runtime.writer.payload["version"] == 3  # type: ignore[attr-defined]


def test_restart_discards_legacy_broker_result_cycle_without_touching_orders() -> None:
    config = _config(steps=2)
    occurred = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    payload: dict[str, object] = {
        "version": 2,
        "signals": {},
        "policy": IQFailurePolicy().dump(0.0, occurred),
        "pending": None,
        # Version 2 was tied to broker P&L and must never be interpreted as
        # candle evidence after the upgrade.
        "martingale_cycle": {"legacy_broker_result_cycle": True},
    }
    runtime = _Runtime()
    runtime.writer = _StateWriter(payload)  # type: ignore[attr-defined]
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: None,
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: config,
        operator_armed=lambda: True,
        utc_clock=lambda: occurred,
        monotonic=lambda: 0.0,
    )

    trader._restore_execution_state(runtime)  # type: ignore[arg-type]

    assert trader._martingale_cycle is None
    assert runtime.writer.payload["version"] == 3  # type: ignore[attr-defined]


def test_ui_protocol_accepts_legacy_payload_with_martingale_off() -> None:
    legacy = UiIqOptionRiskConfig().to_payload()
    for field in (
        "martingale_enabled",
        "martingale_multiplier_basis_points",
        "martingale_max_steps",
        "martingale_max_stake_minor_units",
    ):
        legacy.pop(field)

    restored = UiIqOptionRiskConfig.from_payload(legacy)

    assert restored.martingale_enabled is False
    assert restored.martingale_max_steps == 1
