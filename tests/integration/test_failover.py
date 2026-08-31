from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

from apps.core.orchestrator.leader_lease import LeaderLease
from apps.iqoption_worker.broker_adapter import BrokerAdapterWrapper
from apps.iqoption_worker.order_reconciler import OrderReconciler
from packages.brokers.iqoption_adapter import IQOptionAdapter
from packages.domain.orders import Order, OrderState
from packages.persistence.redis_store import RedisStore
from packages.persistence.sqlite_store import SQLiteStateStore


def _order(order_id: str) -> Order:
    return Order(
        internal_order_id=order_id,
        dedupe_key=f"dedupe-{order_id}",
        account_id="practice",
        strategy_id="strategy",
        asset="EURUSD",
        direction="CALL",
        amount=Decimal("1.00"),
        duration=60,
        state=OrderState.ACCEPTED,
    )


def test_failover_standby_promotes_and_reconciles(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = RedisStore()
        primary = LeaderLease(
            store,
            resource="failover",
            leader_id="primary",
            min_time_between_leader_changes_seconds=0,
        )
        standby = LeaderLease(
            store,
            resource="failover",
            leader_id="standby",
            min_time_between_leader_changes_seconds=0,
        )
        assert await primary.acquire()
        assert await standby.acquire() is False
        await primary.release()
        assert await standby.acquire()
        assert standby.get_fencing_token() == 2

        state = SQLiteStateStore(tmp_path / "state.db")
        state.save_order(_order("recovered"))

        class Client:
            def connect(self) -> dict[str, str]:
                return {"account_type": "practice"}

            def disconnect(self) -> None:
                pass

            def request(self, operation: str, **payload: object) -> object:
                if operation in {"open_orders", "settled_orders"}:
                    return [_order("recovered")]
                return []

        adapter = IQOptionAdapter(Client())
        adapter.connect()
        result = await OrderReconciler(
            state, BrokerAdapterWrapper(adapter), account_id="practice"
        ).reconcile()
        assert result.matched == 1
        assert result.trading_allowed is True
        state.close()

    asyncio.run(scenario())
