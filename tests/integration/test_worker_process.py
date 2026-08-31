from __future__ import annotations

from pathlib import Path

from apps.core.observability.health import HealthChecker
from apps.iqoption_worker.broker_adapter import BrokerAdapterWrapper
from apps.iqoption_worker.connection_manager import ConnectionManager
from apps.iqoption_worker.order_reconciler import OrderReconciler
from apps.iqoption_worker.process import WorkerProcess, WorkerState
from packages.brokers.iqoption_adapter import IQOptionAdapter


class Client:
    def connect(self) -> dict[str, str]:
        return {"account_type": "practice"}

    def disconnect(self) -> None:
        pass

    def request(self, operation: str, **payload: object) -> object:
        if operation in {"open_orders", "settled_orders", "positions"}:
            return []
        if operation == "balance":
            return {"balance": "100.00", "currency": "USD"}
        return []


def test_worker_start_stop_and_health(tmp_path: Path) -> None:
    import asyncio

    asyncio.run(_worker_start_stop_and_health(tmp_path))


async def _worker_start_stop_and_health(tmp_path: Path) -> None:
    from packages.persistence.sqlite_store import SQLiteStateStore

    store = SQLiteStateStore(tmp_path / "state.db")
    adapter = IQOptionAdapter(Client())
    wrapper = BrokerAdapterWrapper(adapter)
    manager = ConnectionManager(adapter)
    worker = WorkerProcess(manager, OrderReconciler(store, wrapper, account_id="practice"))

    await worker.start()
    assert worker.get_state() is WorkerState.READY
    status = worker.health_check()
    assert status["liveness"] is True
    assert status["readiness"] is True
    assert status["trading_readiness"] is True
    await worker.stop()
    assert worker.get_state() is WorkerState.HALTED
    assert worker.health_check()["liveness"] is False
    store.close()


def test_health_checker_separates_readiness() -> None:
    checker = HealthChecker(
        state=lambda: "READ_ONLY",
        connected=lambda: True,
        authenticated=lambda: True,
    )
    status = checker.get_status()
    assert status.liveness is True
    assert status.readiness is True
    assert status.trading_readiness is False
