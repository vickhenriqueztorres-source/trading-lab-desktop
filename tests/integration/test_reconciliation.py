from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from apps.iqoption_worker.broker_adapter import BrokerAdapterWrapper
from apps.iqoption_worker.order_reconciler import OrderReconciler
from packages.brokers.iqoption_adapter import IQOptionAdapter
from packages.domain.orders import Order, OrderState
from packages.persistence.sqlite_store import SQLiteStateStore


class Client:
    def __init__(self, remote: list[Order]) -> None:
        self.remote = remote

    def connect(self) -> dict[str, str]:
        return {"account_type": "practice"}

    def disconnect(self) -> None:
        pass

    def request(self, operation: str, **payload: object) -> object:
        if operation in {"open_orders", "settled_orders"}:
            return self.remote
        if operation in {"balance", "positions"}:
            return []
        return []


def _order(order_id: str, state: OrderState) -> Order:
    return Order(
        internal_order_id=order_id,
        dedupe_key=f"dedupe-{order_id}",
        account_id="practice",
        strategy_id="strategy",
        asset="EURUSD",
        direction="CALL",
        amount=Decimal("1.00"),
        duration=60,
        state=state,
        timestamps={"created": datetime.now(UTC)},
    )


def test_reconciliation_matches_recovers_and_marks_missing(tmp_path: Path) -> None:
    import asyncio

    asyncio.run(_reconciliation_matches_recovers_and_marks_missing(tmp_path))


async def _reconciliation_matches_recovers_and_marks_missing(tmp_path: Path) -> None:
    store = SQLiteStateStore(tmp_path / "state.db")
    local = _order("local", OrderState.ACCEPTED)
    store.save_order(local)
    remote = [_order("local", OrderState.ACCEPTED), _order("recovered", OrderState.ACCEPTED)]
    adapter = IQOptionAdapter(Client(remote))
    adapter.connect()
    result = await OrderReconciler(
        store, BrokerAdapterWrapper(adapter), account_id="practice"
    ).reconcile()
    assert result.matched == 1
    assert result.recovered == 1
    assert result.unknown == 0
    assert store.get_order("recovered") is not None
    store.close()


def test_reconciliation_divergence_disables_trading(tmp_path: Path) -> None:
    import asyncio

    asyncio.run(_reconciliation_divergence_disables_trading(tmp_path))


async def _reconciliation_divergence_disables_trading(tmp_path: Path) -> None:
    store = SQLiteStateStore(tmp_path / "state.db")
    store.save_order(_order("local", OrderState.ACCEPTED))
    adapter = IQOptionAdapter(Client([_order("local", OrderState.REJECTED_REMOTE)]))
    adapter.connect()
    result = await OrderReconciler(
        store, BrokerAdapterWrapper(adapter), account_id="practice"
    ).reconcile()
    assert result.divergences == ("STATUS_DIVERGENCE:local",)
    assert result.trading_allowed is False
    store.close()
