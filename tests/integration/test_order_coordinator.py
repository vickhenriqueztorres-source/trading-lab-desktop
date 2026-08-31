from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from apps.core.orchestrator.order_coordinator import OrderAdmissionError, OrderCoordinator
from apps.core.orchestrator.order_queue import OrderQueue, QueueBackpressureError
from apps.iqoption_worker.broker_adapter import BrokerAdapterWrapper
from packages.brokers.iqoption_adapter import IQOptionAdapter
from packages.domain.orders import OrderIntent, OrderState
from packages.persistence.sqlite_store import SQLiteStateStore


class Client:
    def __init__(self) -> None:
        self.submits = 0

    def connect(self) -> dict[str, str]:
        return {"account_type": "practice"}

    def disconnect(self) -> None:
        pass

    def request(self, operation: str, **payload: object) -> object:
        if operation == "submit_order":
            self.submits += 1
            return {"accepted": True, "broker_order_id": f"remote-{self.submits}"}
        return []


def _intent(dedupe: str = "dedupe") -> OrderIntent:
    return OrderIntent(
        intent_id=f"intent-{dedupe}",
        dedupe_key=dedupe,
        account_id="practice",
        strategy_id="strategy",
        asset="EURUSD",
        direction="CALL",
        amount=Decimal("1.00"),
        duration=60,
    )


def test_coordinator_single_writer_and_idempotency(tmp_path: Path) -> None:
    asyncio.run(_coordinator_single_writer_and_idempotency(tmp_path))


async def _coordinator_single_writer_and_idempotency(tmp_path: Path) -> None:
    store = SQLiteStateStore(tmp_path / "state.db")
    client = Client()
    adapter = IQOptionAdapter(client)
    adapter.connect()
    coordinator = OrderCoordinator(store, BrokerAdapterWrapper(adapter), account_id="practice")
    first, second = await asyncio.gather(
        coordinator.submit_order(_intent()), coordinator.submit_order(_intent())
    )
    assert first.state is OrderState.ACCEPTED
    assert second.state is OrderState.ACCEPTED
    assert client.submits == 1
    store.close()


def test_queue_backpressure_is_explicit() -> None:
    asyncio.run(_queue_backpressure_is_explicit())


async def _queue_backpressure_is_explicit() -> None:
    queue = OrderQueue(maxsize=1)
    await queue.enqueue(_intent())
    with pytest.raises(QueueBackpressureError):
        await queue.enqueue(_intent("other"))


def test_coordinator_requires_leadership(tmp_path: Path) -> None:
    asyncio.run(_coordinator_requires_leadership(tmp_path))


async def _coordinator_requires_leadership(tmp_path: Path) -> None:
    store = SQLiteStateStore(tmp_path / "state.db")
    adapter = IQOptionAdapter(Client())
    adapter.connect()
    coordinator = OrderCoordinator(
        store,
        BrokerAdapterWrapper(adapter),
        account_id="practice",
        leadership_check=lambda: False,
    )
    with pytest.raises(OrderAdmissionError, match="TRADING_NOT_ALLOWED"):
        await coordinator.submit_order(_intent())
    store.close()
