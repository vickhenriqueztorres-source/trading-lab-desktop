from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.core.iqoption_auto_trader import IqOptionAutoTrader
from apps.core.iqoption_connection_safety import IQOptionMessageBudget
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction, OrderRequest, OrderState


def _make_candles(
    prices: list[str],
    *,
    symbol: str = "EURUSD-OTC",
) -> list[MarketCandle]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles: list[MarketCandle] = []
    for index, raw_price in enumerate(prices):
        open_time = base + timedelta(minutes=index)
        price = Decimal(raw_price)
        candles.append(
            MarketCandle(
                broker=Broker.IQ_OPTION,
                broker_symbol=symbol,
                timeframe_seconds=60,
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1),
                open=price,
                high=price + Decimal("0.0001"),
                low=price - Decimal("0.0001"),
                close=price,
                is_closed=True,
            )
        )
    return candles


class FakeReader:
    def __init__(self, state: OrderState, outbox_reason: str | None = None) -> None:
        self.state = state
        self.outbox_reason = outbox_reason

    def one(self, _table: str, _key: str, _value: str) -> dict[str, str]:
        return {"state": self.state.value}

    def outbox_for_intent(self, _intent_id: str) -> dict[str, str] | None:
        if self.outbox_reason is None:
            return None
        return {"state_reason": self.outbox_reason}

    def list_nonterminal_orders(self) -> list[dict[str, str]]:
        return []


class FakeHealthGate:
    def block_scope(self, *_args: str) -> None:
        return None

    def clear_scope(self, *_args: str) -> None:
        return None


class FakeRuntime:
    def __init__(
        self,
        state: OrderState = OrderState.ACCEPTED,
        outbox_reason: str | None = None,
    ) -> None:
        self.reader = FakeReader(state, outbox_reason)
        self.health_gate = FakeHealthGate()
        self.requests: list[OrderRequest] = []
        self.failure: Exception | None = None
        self.events: list[tuple[str, dict[str, object]]] = []
        self.event_sink = SimpleNamespace(
            emit=lambda name, **fields: self.events.append((name, fields))
        )

    def submit(self, request: OrderRequest) -> SimpleNamespace:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(order_id="iq-order-1", intent_id="iq-intent-1")


class FakeClient:
    def __init__(self, candles: list[MarketCandle]) -> None:
        self.candles = candles
        self.market_requests: list[tuple[str, str, int, int]] = []

    def market_history(
        self,
        symbol: str,
        *,
        style: str,
        count: int,
        timeframe_seconds: int,
    ) -> tuple[list[object], list[MarketCandle]]:
        self.market_requests.append((symbol, style, count, timeframe_seconds))
        return [], self.candles


def _falling_prices() -> list[str]:
    return [str(Decimal("1.1000") - Decimal(index) * Decimal("0.0010")) for index in range(20)]


def test_signal_uses_broker_candles_and_persists_through_core() -> None:
    client = FakeClient(_make_candles(_falling_prices()))
    runtime = FakeRuntime()
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="EURUSD-OTC"),
        operator_armed=lambda: True,
    )

    trader._evaluate_cycle()

    assert client.market_requests == [("EURUSD-OTC", "candles", 20, 60)]
    assert len(runtime.requests) == 1
    request = runtime.requests[0]
    assert request.broker is Broker.IQ_OPTION
    assert request.direction is Direction.CALL
    assert request.product == "BINARY_OPTION"
    assert trader.status_reason.startswith("ORDEM_ACEITA:")

    trader._evaluate_cycle()
    assert len(runtime.requests) == 1
    assert trader.status_reason.startswith("SINAL_CONSUMIDO:")


def test_core_submission_failure_is_never_reported_as_success() -> None:
    client = FakeClient(_make_candles(_falling_prices()))
    runtime = FakeRuntime()
    runtime.failure = RuntimeError("dispatch failed")
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="EURUSD-OTC"),
        operator_armed=lambda: True,
    )

    trader._evaluate_cycle()

    assert len(runtime.requests) == 1
    assert trader.status_reason == "IQOPTION_ORDER_SUBMISSION_FAILED"
    assert "ACEITA" not in trader.status_reason

    trader._evaluate_cycle()
    assert trader.status_reason == "IQOPTION_ORDER_SUBMISSION_FAILED"


def test_market_data_budget_blocks_request_before_internal_limit_is_exceeded() -> None:
    client = FakeClient(_make_candles(_falling_prices()))
    runtime = FakeRuntime()
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="EURUSD-OTC"),
        operator_armed=lambda: False,
        message_budget=IQOptionMessageBudget(limit=1, pressure_at=1),
    )

    trader._evaluate_cycle()
    trader._evaluate_cycle()

    assert len(client.market_requests) == 1
    assert trader.status_reason == "IQOPTION_MESSAGE_BUDGET_EXHAUSTED"
    assert runtime.events == [
        (
            "iqoption_message_budget_pressure",
            {"used_in_window": 1, "limit": 1},
        )
    ]


def test_health_gate_failure_reason_remains_visible_after_scan_continues() -> None:
    client = FakeClient(_make_candles(_falling_prices()))
    runtime = FakeRuntime()
    runtime.failure = RuntimeError("Health Gate blocked: HG_AUTH_REQUIRED")
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="EURUSD-OTC"),
        operator_armed=lambda: True,
    )

    trader._evaluate_cycle()
    assert trader.status_reason == "HG_AUTH_REQUIRED"

    trader._evaluate_cycle()
    assert len(runtime.requests) == 1
    assert trader.status_reason == "HG_AUTH_REQUIRED"


def test_remote_minimum_rejection_is_stable_and_blocks_more_scan_dispatches() -> None:
    client = FakeClient(_make_candles(_falling_prices()))
    runtime = FakeRuntime(
        OrderState.REJECTED,
        "Cannot purchase an option: your investment amount is smaller than the allowed minimum.",
    )
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="EURUSD-OTC"),
        operator_armed=lambda: True,
    )

    trader._evaluate_cycle()
    trader._evaluate_cycle()

    assert len(runtime.requests) == 1
    assert trader.status_reason == "IQOPTION_STAKE_BELOW_BROKER_MINIMUM"

    trader.begin_new_run()
    trader._evaluate_cycle()
    assert len(runtime.requests) == 2


@pytest.mark.parametrize(
    "broker_reason",
    [
        "Cannot purchase an option (active is suspended)",
        "Cannot purchase an option (the asset is not available at the moment).",
    ],
)
def test_suspended_or_unavailable_auto_asset_does_not_freeze_other_assets(
    broker_reason: str,
) -> None:
    class DynamicClient:
        def __init__(self) -> None:
            self.market_requests: list[str] = []

        def market_history(
            self,
            symbol: str,
            *,
            style: str,
            count: int,
            timeframe_seconds: int,
        ) -> tuple[list[object], list[MarketCandle]]:
            assert (style, count, timeframe_seconds) == ("candles", 20, 60)
            self.market_requests.append(symbol)
            return [], _make_candles(_falling_prices(), symbol=symbol)

    client = DynamicClient()
    runtime = FakeRuntime(
        OrderState.REJECTED,
        broker_reason,
    )
    trader = IqOptionAutoTrader(
        supervisor_provider=lambda: SimpleNamespace(client=client),
        runtime_provider=lambda: runtime,  # type: ignore[arg-type]
        risk_config_provider=lambda: IqOptionRiskConfig(symbol="AUTO"),
        operator_armed=lambda: True,
    )

    trader._evaluate_cycle()
    assert trader.status_reason.startswith("IQOPTION_ACTIVE_SUSPENDED")
    assert [request.symbol for request in runtime.requests] == ["EURUSD-OTC"]

    runtime.reader.state = OrderState.ACCEPTED
    runtime.reader.outbox_reason = None
    trader._evaluate_cycle()

    assert client.market_requests == ["EURUSD-OTC", "GBPUSD-OTC"]
    assert [request.symbol for request in runtime.requests] == ["EURUSD-OTC", "GBPUSD-OTC"]
    assert trader.status_reason.startswith("ORDEM_ACEITA: GBP/USD OTC")
