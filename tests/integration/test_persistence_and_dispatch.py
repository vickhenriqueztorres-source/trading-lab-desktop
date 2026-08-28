from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.core.coordinator import OrderCoordinator
from apps.core.health import HealthGate
from apps.core.recovery import RecoveryCoordinator
from apps.simulated_worker.worker import SimulatedWorker
from packages.domain.models import OrderRequest, OrderState, WorkerOutcome
from packages.persistence.migrations import MIGRATIONS
from packages.persistence.reader import StateReader
from packages.persistence.writer import (
    PersistenceError,
    ReservationReleaseBlocked,
    SingleDatabaseWriter,
)
from packages.protocol.messages import WorkerSubmissionResult


def build_coordinator(
    db_path: Path,
    outcome: WorkerOutcome,
    *,
    on_receive=None,
    fault_injector=None,
):
    writer = SingleDatabaseWriter(db_path, fault_injector=fault_injector)
    reader = StateReader(db_path)
    worker = SimulatedWorker([outcome], on_receive=on_receive)
    gate = HealthGate()
    coordinator = OrderCoordinator(writer, worker, gate)
    return writer, reader, worker, gate, coordinator


@pytest.mark.parametrize("failure_stage", ["after_intent", "after_reservation", "after_outbox"])
def test_atomicity_rolls_back_every_financial_record(
    tmp_path: Path, order_request: OrderRequest, failure_stage: str
) -> None:
    def inject(stage: str) -> None:
        if stage == failure_stage:
            raise OSError("simulated storage failure")

    writer, reader, worker, gate, coordinator = build_coordinator(
        tmp_path / "state.db",
        WorkerOutcome.ACCEPTED,
        fault_injector=inject,
    )

    with pytest.raises(PersistenceError):
        coordinator.submit(order_request)

    assert reader.count("trade_intents") == 0
    assert reader.count("risk_reservations") == 0
    assert reader.count("outbox_messages") == 0
    assert reader.count("orders") == 0
    assert worker.received == []
    assert gate.state.reason_code == "DB_WRITE_FAILED"
    writer.close()


def test_worker_observes_committed_records_before_dispatch(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    db_path = tmp_path / "state.db"
    reader = StateReader(db_path)

    def prove_commit(command) -> None:
        assert reader.count("trade_intents") == 1
        assert reader.count("risk_reservations") == 1
        assert reader.count("outbox_messages") == 1
        assert reader.outbox_for_intent(command.intent_id)["state"] == "DISPATCHING"

    writer, _, worker, _, coordinator = build_coordinator(
        db_path, WorkerOutcome.ACCEPTED, on_receive=prove_commit
    )
    coordinator.submit(order_request)
    assert len(worker.received) == 1
    writer.close()


def test_pending_outbox_survives_restart(tmp_path: Path, order_request: OrderRequest) -> None:
    db_path = tmp_path / "state.db"
    writer, reader, _, _, coordinator = build_coordinator(db_path, WorkerOutcome.ACCEPTED)
    persisted = coordinator.submit(order_request, dispatch=False)
    writer.close()

    restarted_writer = SingleDatabaseWriter(db_path)
    row = reader.one("outbox_messages", "message_id", persisted.message_id)
    assert row is not None
    assert row["state"] == "PENDING"
    report = RecoveryCoordinator(restarted_writer, reader, HealthGate()).recover()
    assert report.safe_pending_message_ids == (persisted.message_id,)
    restarted_writer.close()


def test_pre_ipc_v1_outbox_payload_recovers_order_id_from_order_projection(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    db_path = tmp_path / "state.db"
    writer, _, _, _, coordinator = build_coordinator(db_path, WorkerOutcome.ACCEPTED)
    persisted = coordinator.submit(order_request, dispatch=False)
    writer.close()

    connection = sqlite3.connect(db_path)
    row = connection.execute(
        "SELECT payload FROM outbox_messages WHERE message_id = ?",
        (persisted.message_id,),
    ).fetchone()
    payload = json.loads(row[0])
    del payload["order_id"]
    connection.execute(
        "UPDATE outbox_messages SET payload = ? WHERE message_id = ?",
        (json.dumps(payload), persisted.message_id),
    )
    connection.commit()
    connection.close()

    restarted = SingleDatabaseWriter(db_path)
    claimed = restarted.claim_next_message()
    assert claimed is not None
    assert claimed.order_id == persisted.order_id
    restarted.close()


def test_confirmed_dispatch_state_survives_restart(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    db_path = tmp_path / "state.db"
    writer, reader, _, _, coordinator = build_coordinator(db_path, WorkerOutcome.ACCEPTED)
    persisted = coordinator.submit(order_request)
    row = reader.one("outbox_messages", "message_id", persisted.message_id)
    assert row["state"] == "DISPATCHED"
    assert row["attempt_count"] == 1
    writer.close()

    restarted_writer = SingleDatabaseWriter(db_path)
    assert (
        reader.one("outbox_messages", "message_id", persisted.message_id)["state"] == "DISPATCHED"
    )
    restarted_writer.close()


def test_confirmed_rejection_reason_is_persisted_for_diagnostics(
    tmp_path: Path,
    order_request: OrderRequest,
) -> None:
    class RejectedWorker:
        def submit_order(self, command):
            return WorkerSubmissionResult(
                outcome=WorkerOutcome.REJECTED,
                broker_order_id=None,
                response_message_id="response-rejected",
                correlation_id=command.correlation_id,
                causation_id=command.message_id,
                reason_code="DERIV_INVALID_REQUEST",
            )

    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    reader = StateReader(db_path)
    coordinator = OrderCoordinator(writer, RejectedWorker(), HealthGate())

    persisted = coordinator.submit(order_request)

    outbox = reader.one("outbox_messages", "message_id", persisted.message_id)
    assert outbox is not None
    assert outbox["state"] == "DISPATCHED"
    assert outbox["state_reason"] == "DERIV_INVALID_REQUEST"
    assert reader.one("orders", "order_id", persisted.order_id)["state"] == "REJECTED"
    assert (
        reader.one("risk_reservations", "reservation_id", persisted.reservation_id)["state"]
        == "RELEASED"
    )
    writer.close()


def test_timeout_becomes_ambiguous_and_keeps_exposure(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    writer, reader, _, gate, coordinator = build_coordinator(
        tmp_path / "state.db", WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND
    )
    persisted = coordinator.submit(order_request)

    assert reader.one("outbox_messages", "message_id", persisted.message_id)["state"] == "AMBIGUOUS"
    assert reader.one("orders", "order_id", persisted.order_id)["state"] == "UNKNOWN"
    assert (
        reader.one("risk_reservations", "reservation_id", persisted.reservation_id)["state"]
        == "ACTIVE"
    )
    assert (
        reader.one("outbox_messages", "message_id", persisted.message_id)["dispatched_at"] is None
    )
    assert gate.state.reason_code == "HG_ORDER_UNKNOWN"
    writer.close()


def test_unknown_reservation_requires_explicit_reconciliation(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    writer, reader, _, _, coordinator = build_coordinator(
        tmp_path / "state.db", WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND
    )
    persisted = coordinator.submit(order_request)

    with pytest.raises(ReservationReleaseBlocked):
        writer.release_reservation(persisted.reservation_id)
    assert (
        reader.one("risk_reservations", "reservation_id", persisted.reservation_id)["state"]
        == "ACTIVE"
    )

    writer.release_after_reconciliation(
        persisted.reservation_id,
        OrderState.REJECTED,
        "manual-review-case-001",
    )
    released = reader.one("risk_reservations", "reservation_id", persisted.reservation_id)
    assert released["state"] == "RELEASED"
    assert released["reconciliation_evidence"] == "manual-review-case-001"
    assert reader.one("orders", "order_id", persisted.order_id)["state"] == "REJECTED"
    writer.close()


def test_database_failure_never_reaches_worker(tmp_path: Path, order_request: OrderRequest) -> None:
    def fail_before_commit(stage: str) -> None:
        if stage == "before_commit":
            raise OSError("simulated disk unavailable")

    writer, _, worker, gate, coordinator = build_coordinator(
        tmp_path / "state.db",
        WorkerOutcome.ACCEPTED,
        fault_injector=fail_before_commit,
    )
    with pytest.raises(PersistenceError):
        coordinator.submit(order_request)
    assert worker.received == []
    assert gate.state.is_open is False
    writer.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    reader = StateReader(db_path)
    assert reader.count("schema_migrations") == len(MIGRATIONS)
    writer.close()

    restarted_writer = SingleDatabaseWriter(db_path)
    assert reader.count("schema_migrations") == len(MIGRATIONS)
    restarted_writer.close()


def test_interrupted_claim_is_unknown_on_recovery(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    db_path = tmp_path / "state.db"
    writer, reader, _, _, coordinator = build_coordinator(db_path, WorkerOutcome.ACCEPTED)
    persisted = coordinator.submit(order_request, dispatch=False)
    claimed = writer.claim_next_message(datetime.now(UTC))
    assert claimed is not None
    writer.close()

    restarted_writer = SingleDatabaseWriter(db_path)
    gate = HealthGate()
    report = RecoveryCoordinator(restarted_writer, reader, gate).recover()
    assert report.ambiguous_message_ids == (persisted.message_id,)
    assert reader.one("orders", "order_id", persisted.order_id)["state"] == "UNKNOWN"
    assert (
        reader.one("risk_reservations", "reservation_id", persisted.reservation_id)["state"]
        == "ACTIVE"
    )
    assert gate.state.reason_code == "HG_ORDER_UNKNOWN"
    restarted_writer.close()


def test_concurrent_claim_returns_one_dispatchable_message(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    db_path = tmp_path / "state.db"
    writer, _, _, _, coordinator = build_coordinator(db_path, WorkerOutcome.ACCEPTED)
    coordinator.submit(order_request, dispatch=False)
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait()
        return writer.claim_next_message(datetime.now(UTC))

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(lambda _: claim(), range(2)))

    assert sum(command is not None for command in claimed) == 1
    writer.close()


def test_expired_command_is_cancelled_without_worker_call(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    expired = replace(order_request, deadline_at=datetime.now(UTC) - timedelta(seconds=1))
    writer, reader, worker, _, coordinator = build_coordinator(
        tmp_path / "state.db", WorkerOutcome.ACCEPTED
    )
    persisted = coordinator.submit(expired)
    assert worker.received == []
    assert reader.one("outbox_messages", "message_id", persisted.message_id)["state"] == "CANCELLED"
    assert (
        reader.one("risk_reservations", "reservation_id", persisted.reservation_id)["state"]
        == "RELEASED"
    )
    writer.close()
