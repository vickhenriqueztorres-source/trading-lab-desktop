from __future__ import annotations

from decimal import Decimal

import pytest

from packages.brokers.iqoption_adapter import IQOptionAdapter
from packages.brokers.port import BrokerPort, Capability, CapabilityMap, NetworkTransientError
from packages.domain.orders import OrderIntent, OrderState


class FakeClient:
    def __init__(self) -> None:
        self.connected = False

    def connect(self) -> dict[str, str]:
        self.connected = True
        return {"account_type": "practice"}

    def disconnect(self) -> None:
        self.connected = False

    def request(self, operation: str, **payload: object) -> object:
        if operation == "submit_order":
            return {"accepted": True, "broker_order_id": "iq-1"}
        if operation == "subscribe_candles":
            return "sub-1"
        return []


def _intent() -> OrderIntent:
    return OrderIntent(
        intent_id="intent-1",
        dedupe_key="dedupe-1",
        account_id="practice-1",
        strategy_id="strategy-1",
        asset="EURUSD",
        direction="CALL",
        amount=Decimal("1.00"),
        duration=60,
    )


def test_iqoption_adapter_implements_port_and_practice_submission() -> None:
    adapter = IQOptionAdapter(FakeClient())
    assert isinstance(adapter, BrokerPort)
    assert adapter.capability_map.supports(Capability.BALANCE_QUERY)
    adapter.connect()
    result = adapter.submit_order(_intent())
    assert result.state is OrderState.ACCEPTED
    assert result.broker_order_id == "iq-1"


def test_capability_map_is_explicit() -> None:
    capabilities = CapabilityMap(BALANCE_QUERY=True)
    assert capabilities.supports("BALANCE_QUERY")
    assert not capabilities.supports(Capability.CLIENT_IDEMPOTENCY)


def test_unconnected_adapter_fails_closed() -> None:
    adapter = IQOptionAdapter(FakeClient())
    with pytest.raises(NetworkTransientError):
        adapter.get_balance()
