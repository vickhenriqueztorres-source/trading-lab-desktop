from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.core.coordinator import AccountCommandSerializer, OrderCoordinator
from apps.core.health import HealthGate
from apps.simulated_worker.worker import SimulatedWorker
from packages.domain.models import BrokerEvent, OrderRequest, OrderState, WorkerOutcome
from packages.persistence.reader import StateReader
from packages.persistence.writer import (
    AccountBusyError,
    InvalidOrderTransition,
    SingleDatabaseWriter,
)


def test_duplicate_event_is_idempotent_and_does_not_duplicate_pnl(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    reader = StateReader(db_path)
    coordinator = OrderCoordinator(writer, SimulatedWorker([WorkerOutcome.ACCEPTED]), HealthGate())
    persisted = coordinator.submit(order_request)
    opened = BrokerEvent(
        event_id="evt-open",
        intent_id=persisted.intent_id,
        new_state=OrderState.OPEN,
        occurred_at=datetime.now(UTC),
    )
    settled = BrokerEvent(
        event_id="evt-settled",
        intent_id=persisted.intent_id,
        new_state=OrderState.SETTLED,
        occurred_at=datetime.now(UTC),
        realized_pnl_minor=750,
    )
    assert writer.apply_broker_event(opened) is True
    assert writer.apply_broker_event(settled) is True
    assert writer.apply_broker_event(settled) is False

    order = reader.one("orders", "order_id", persisted.order_id)
    assert order["state"] == "SETTLED"
    assert order["realized_pnl_minor"] == 750
    assert reader.count("processed_order_events") == 2
    assert (
        reader.one("risk_reservations", "reservation_id", persisted.reservation_id)["state"]
        == "RELEASED"
    )
    writer.close()


def test_out_of_order_event_cannot_regress_terminal_state(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    reader = StateReader(db_path)
    coordinator = OrderCoordinator(writer, SimulatedWorker([WorkerOutcome.REJECTED]), HealthGate())
    persisted = coordinator.submit(order_request)
    late_event = BrokerEvent(
        event_id="evt-late-open",
        intent_id=persisted.intent_id,
        new_state=OrderState.OPEN,
        occurred_at=datetime.now(UTC),
    )
    with pytest.raises(InvalidOrderTransition):
        writer.apply_broker_event(late_event)
    assert reader.one("orders", "order_id", persisted.order_id)["state"] == "REJECTED"
    assert reader.count("processed_order_events") == 0
    writer.close()


def test_two_concurrent_attempts_for_one_account_create_one_submission(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    reader = StateReader(db_path)
    worker = SimulatedWorker([WorkerOutcome.ACCEPTED, WorkerOutcome.ACCEPTED])
    serializer = AccountCommandSerializer()
    gate = HealthGate()
    coordinator = OrderCoordinator(
        writer,
        worker,
        gate,
        serializer=serializer,
    )
    barrier = threading.Barrier(2)

    def submit(request: OrderRequest):
        barrier.wait()
        try:
            return coordinator.submit(request)
        except AccountBusyError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, [order_request, order_request]))

    assert sum(result is not None for result in results) == 1
    assert len(worker.received) == 1
    assert reader.count("trade_intents") == 1
    assert reader.count("risk_reservations") == 1
    assert reader.count("outbox_messages") == 1
    assert gate.state.is_open is True
    writer.close()
