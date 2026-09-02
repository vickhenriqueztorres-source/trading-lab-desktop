from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from packages.brokers.iqoption_adapter import IQOptionAdapter
from packages.brokers.port import UnsupportedCapabilityError
from packages.domain.orders import OrderIntent, OrderState


class FakeDirectIQClient:
    def __init__(self, *, fail_buy: bool = False, timeout_buy: bool = False) -> None:
        self.fail_buy = fail_buy
        self.timeout_buy = timeout_buy
        self.connected = True
        self.authenticated = True
        self.orders: list[dict[str, Any]] = []

    def connect(self) -> dict[str, Any]:
        return {"account_type": "practice"}

    def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    def buy(self, amount: float, active: str, action: str, exp: int) -> tuple[bool, int | None]:
        if self.timeout_buy:
            raise TimeoutError("IQ Option gateway timed out")
        if self.fail_buy:
            return False, None
        order_id = len(self.orders) + 100001
        self.orders.append(
            {"id": order_id, "amount": amount, "active": active, "action": action, "exp": exp}
        )
        return True, order_id

    def request(self, operation: str, **payload: Any) -> Any:
        if operation == "balance":
            return {"amount": 1000.0, "currency": "USD"}
        if operation == "open_orders":
            return []
        if operation == "settled_orders":
            return []
        if operation == "positions":
            return []
        raise UnsupportedCapabilityError(f"Operation {operation} unsupported in fake client")


def _sample_intent(amount_cents: int = 100) -> OrderIntent:
    return OrderIntent(
        intent_id=str(uuid4()),
        dedupe_key=str(uuid4()),
        account_id="PRACTICE_ACCOUNT",
        strategy_id="strat-test-demo",
        asset="EURUSD",
        direction="call",
        amount=Decimal(amount_cents) / Decimal(100),
        duration=1,
    )


def test_iqoption_force_execution_single_dollar_order() -> None:
    client = FakeDirectIQClient()
    adapter = IQOptionAdapter(client, practice_only=True, force_execution=True)
    adapter.connect()

    intent = _sample_intent(100)  # 1.00 USD
    result = adapter.submit_order(intent)

    assert result.state is OrderState.ACCEPTED
    assert result.broker_order_id is not None
    assert result.internal_order_id == intent.intent_id
    assert len(client.orders) == 1
    assert client.orders[0]["amount"] == 1.0
    assert client.orders[0]["active"] == "EURUSD"


def test_iqoption_force_execution_timeout_yields_unknown_without_blind_retry() -> None:
    client = FakeDirectIQClient(timeout_buy=True)
    adapter = IQOptionAdapter(client, practice_only=True, force_execution=True)
    adapter.connect()

    intent = _sample_intent(100)
    result = adapter.submit_order(intent)

    assert result.state is OrderState.UNKNOWN
    assert result.retry_allowed is False
    assert result.reconciliation_required is True
    assert result.error_code == "ORDER_UNKNOWN"


def test_iqoption_force_execution_circuit_breaker_5_trades_or_5_dollars() -> None:
    client = FakeDirectIQClient()
    adapter = IQOptionAdapter(client, practice_only=True, force_execution=True)
    adapter.connect()

    max_trades = 5
    max_loss = Decimal("5.0")
    loss_per_trade = Decimal("1.0")
    total_loss = Decimal("0.0")
    executed_trades = 0

    for _ in range(10):
        # Stop criteria check
        if executed_trades >= max_trades:
            break
        if total_loss >= max_loss:
            break

        intent = _sample_intent(100)
        res = adapter.submit_order(intent)
        assert res.state is OrderState.ACCEPTED

        executed_trades += 1
        total_loss += loss_per_trade

    assert executed_trades == 5
    assert total_loss == Decimal("5.0")
    assert len(client.orders) == 5
