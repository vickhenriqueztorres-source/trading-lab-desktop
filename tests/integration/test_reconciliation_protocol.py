from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from apps.core.coordinator import OrderCoordinator, PersistedOrder
from apps.core.health import HealthGate
from apps.core.reconciliation import (
    ReconciliationCoordinator,
    ReconciliationOutcome,
)
from apps.core.runtime import CoreRuntime
from apps.core.worker_client import StatusQueryError
from apps.core.worker_supervisor import WorkerSupervisor
from apps.simulated_worker.broker_store import SimulatedBrokerStore
from apps.simulated_worker.scenarios import WorkerScenario
from packages.domain.models import (
    Broker,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderRequest,
    OrderState,
    OrderStatusQuery,
)
from packages.persistence.database import open_writer_connection
from packages.persistence.migrations import MIGRATIONS, apply_migrations
from packages.persistence.reader import StateReader
from packages.persistence.writer import ReconciliationApplyStatus, SingleDatabaseWriter
from packages.protocol.errors import ProtocolErrorCode


@dataclass(frozen=True, slots=True)
class AmbiguousFixture:
    database_path: Path
    broker_store_path: Path
    persisted: PersistedOrder
    request: OrderRequest


def request(name: str = "reconciliation") -> OrderRequest:
    return OrderRequest(
        correlation_id=f"corr-{name}",
        broker=Broker.DERIV,
        account_id="demo-account-1",
        product="DIGITAL_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1_000, "USD"),
        strategy_id="strategy-test",
        strategy_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )


def create_ambiguous(
    root: Path,
    scenario: WorkerScenario = WorkerScenario.ACCEPT_BUT_DROP_RESPONSE,
) -> AmbiguousFixture:
    database_path = root / "state.db"
    broker_store_path = root / "simulated_broker.db"
    writer = SingleDatabaseWriter(database_path)
    reader = StateReader(database_path)
    gate = HealthGate()
    supervisor = WorkerSupervisor(
        gate,
        scenario=scenario,
        response_timeout=0.2,
        heartbeat_interval=10.0,
        broker_store_path=broker_store_path,
    )
    client = supervisor.start()
    order_request = request(scenario.value.lower())
    try:
        persisted = OrderCoordinator(writer, client, gate).submit(order_request)
    finally:
        supervisor.shutdown()
        writer.close()
    order = reader.one("orders", "order_id", persisted.order_id)
    outbox = reader.one("outbox_messages", "message_id", persisted.message_id)
    reservation = reader.one("risk_reservations", "reservation_id", persisted.reservation_id)
    assert order is not None and order["state"] == "UNKNOWN"
    assert outbox is not None and outbox["state"] == "AMBIGUOUS"
    assert reservation is not None and reservation["state"] == "ACTIVE"
    return AmbiguousFixture(database_path, broker_store_path, persisted, order_request)


def reconcile(
    fixture: AmbiguousFixture,
    scenario: WorkerScenario = WorkerScenario.ACCEPT,
    *,
    max_query_attempts: int = 2,
) -> tuple[object, StateReader]:
    writer = SingleDatabaseWriter(fixture.database_path)
    reader = StateReader(fixture.database_path)
    gate = HealthGate()
    gate.block("HG_ORDER_UNKNOWN")
    supervisor = WorkerSupervisor(
        gate,
        scenario=scenario,
        response_timeout=0.2,
        heartbeat_interval=10.0,
        broker_store_path=fixture.broker_store_path,
    )
    client = supervisor.start()
    try:
        report = ReconciliationCoordinator(
            writer,
            reader,
            client,
            gate,
            max_query_attempts=max_query_attempts,
            query_timeout=0.3,
            retry_delay=0,
        ).reconcile_all()
    finally:
        supervisor.shutdown()
        writer.close()
    return report, reader


def query_from_fixture(fixture: AmbiguousFixture) -> OrderStatusQuery:
    return OrderStatusQuery(
        correlation_id=fixture.request.correlation_id,
        intent_id=fixture.persisted.intent_id,
        order_id=fixture.persisted.order_id,
        client_order_ref=fixture.persisted.order_id,
        broker=fixture.request.broker,
        account_id=fixture.request.account_id,
        product=fixture.request.product,
        symbol=fixture.request.symbol,
        direction=fixture.request.direction,
        amount=fixture.request.amount,
    )


def test_rec_01_02_acceptance_lost_then_status_evidence_resolves_unknown(
    tmp_path: Path,
) -> None:
    fixture = create_ambiguous(tmp_path)
    report, reader = reconcile(fixture)

    assert report.results[0].outcome is ReconciliationOutcome.RESOLVED
    order = reader.one("orders", "order_id", fixture.persisted.order_id)
    outbox = reader.one("outbox_messages", "message_id", fixture.persisted.message_id)
    reservation = reader.one(
        "risk_reservations", "reservation_id", fixture.persisted.reservation_id
    )
    assert order is not None and order["state"] == "ACCEPTED"
    assert order["resolution_source"] == "STATUS_QUERY"
    assert order["resolution_evidence_id"] is not None
    assert outbox is not None and outbox["state"] == "RECONCILED"
    assert reservation is not None and reservation["state"] == "ACTIVE"


def test_rec_03_04_rejection_lost_releases_reservation_exactly_once(tmp_path: Path) -> None:
    fixture = create_ambiguous(tmp_path, WorkerScenario.REJECT_BUT_DROP_RESPONSE)
    report, reader = reconcile(fixture)

    assert report.results[0].order_state is OrderState.REJECTED
    reservation = reader.one(
        "risk_reservations", "reservation_id", fixture.persisted.reservation_id
    )
    assert reservation is not None and reservation["state"] == "RELEASED"
    assert reservation["reconciliation_evidence"] is not None
    assert reader.count("reconciliation_evidence") == 1


def test_rec_05_not_found_never_resolves_possible_send(tmp_path: Path) -> None:
    fixture = create_ambiguous(tmp_path)
    report, reader = reconcile(fixture, WorkerScenario.STATUS_NOT_FOUND)

    assert report.results[0].outcome is ReconciliationOutcome.UNRESOLVED
    assert report.results[0].reason_code == "RECONCILIATION_NOT_FOUND"
    assert reader.one("orders", "order_id", fixture.persisted.order_id)["state"] == "UNKNOWN"
    assert (
        reader.one("risk_reservations", "reservation_id", fixture.persisted.reservation_id)["state"]
        == "ACTIVE"
    )


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        (WorkerScenario.STATUS_CONFLICT_ACCOUNT, "ACCOUNT_CONFLICT"),
        (WorkerScenario.STATUS_CONFLICT_AMOUNT, "AMOUNT_CONFLICT"),
        (WorkerScenario.STATUS_CONFLICT_CURRENCY, "CURRENCY_CONFLICT"),
        (WorkerScenario.STATUS_CONFLICT_SYMBOL, "SYMBOL_CONFLICT"),
    ],
)
def test_rec_06_to_09_matching_conflicts_require_manual_review(
    tmp_path: Path,
    scenario: WorkerScenario,
    reason: str,
) -> None:
    fixture = create_ambiguous(tmp_path)
    report, reader = reconcile(fixture, scenario)

    result = report.results[0]
    assert result.outcome is ReconciliationOutcome.MANUAL_REVIEW_REQUIRED
    assert result.reason_code == reason
    assert reader.one("orders", "order_id", fixture.persisted.order_id)["state"] == "UNKNOWN"
    assert (
        reader.one("risk_reservations", "reservation_id", fixture.persisted.reservation_id)["state"]
        == "ACTIVE"
    )


def test_rec_10_broker_id_conflict_is_detected_when_both_ids_exist(tmp_path: Path) -> None:
    fixture = create_ambiguous(tmp_path)
    writer = SingleDatabaseWriter(fixture.database_path)
    store = SimulatedBrokerStore(fixture.broker_store_path)
    evidence = store.query_order(query_from_fixture(fixture))
    store.close()
    assert evidence is not None
    with writer._lock:  # test-only setup of an already-known broker identifier
        writer._connection.execute(
            "UPDATE orders SET broker_order_id = ? WHERE order_id = ?",
            ("SIM-OTHER", fixture.persisted.order_id),
        )
    attempt_id = str(uuid4())
    writer.begin_reconciliation_attempt(
        attempt_id,
        fixture.persisted.order_id,
        fixture.request.correlation_id,
    )
    applied = writer.apply_reconciliation_evidence(attempt_id, evidence)
    writer.close()

    assert applied.status is ReconciliationApplyStatus.CONFLICT
    assert applied.reason_code == "BROKER_ORDER_ID_CONFLICT"


def test_rec_11_12_repeated_evidence_is_idempotent_and_conflict_cannot_regress(
    tmp_path: Path,
) -> None:
    fixture = create_ambiguous(tmp_path)
    writer = SingleDatabaseWriter(fixture.database_path)
    reader = StateReader(fixture.database_path)
    store = SimulatedBrokerStore(fixture.broker_store_path)
    evidence = store.query_order(query_from_fixture(fixture))
    store.close()
    assert evidence is not None

    first_attempt = str(uuid4())
    writer.begin_reconciliation_attempt(
        first_attempt, fixture.persisted.order_id, fixture.request.correlation_id
    )
    first = writer.apply_reconciliation_evidence(first_attempt, evidence)
    second_attempt = str(uuid4())
    writer.begin_reconciliation_attempt(
        second_attempt, fixture.persisted.order_id, fixture.request.correlation_id
    )
    repeated = writer.apply_reconciliation_evidence(second_attempt, evidence)
    conflicting = replace(
        evidence,
        evidence_id=str(uuid4()),
        external_status=ExternalOrderStatus.REJECTED,
    )
    third_attempt = str(uuid4())
    writer.begin_reconciliation_attempt(
        third_attempt, fixture.persisted.order_id, fixture.request.correlation_id
    )
    conflict = writer.apply_reconciliation_evidence(third_attempt, conflicting)
    writer.close()

    assert first.status is ReconciliationApplyStatus.RESOLVED
    assert repeated.status is ReconciliationApplyStatus.IDEMPOTENT
    assert conflict.status is ReconciliationApplyStatus.CONFLICT
    assert reader.one("orders", "order_id", fixture.persisted.order_id)["state"] == "ACCEPTED"
    assert reader.count("reconciliation_evidence") == 2


def test_rec_13_14_worker_and_core_restart_preserve_evidence_and_unknown(tmp_path: Path) -> None:
    fixture = create_ambiguous(tmp_path)
    restarted_writer = SingleDatabaseWriter(fixture.database_path)
    restarted_writer.close()

    report, reader = reconcile(fixture)
    assert report.results[0].order_state is OrderState.ACCEPTED
    assert reader.count("reconciliation_evidence") == 1
    metrics = SimulatedBrokerStore.read_metrics(fixture.broker_store_path)
    assert metrics["submit_count"] == 1


def test_rec_15_dual_restart_resolves_without_resubmission(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    first = CoreRuntime(profile, worker_scenario=WorkerScenario.ACCEPT_BUT_DROP_RESPONSE)
    first.start()
    persisted = first.submit(request("dual-restart"))
    first.shutdown()

    second = CoreRuntime(profile)
    second.start()
    try:
        order = second.reader.one("orders", "order_id", persisted.order_id)
        assert order is not None and order["state"] == "ACCEPTED"
        assert second.reconciliation_report.resolved_count == 1
        assert (
            SimulatedBrokerStore.read_metrics(second.simulated_broker_store_path)["submit_count"]
            == 1
        )
    finally:
        second.shutdown()


class UnavailableStatusWorker:
    def __init__(self) -> None:
        self.query_count = 0

    def query_order_status(self, query: OrderStatusQuery, *, timeout: float | None = None):
        del query, timeout
        self.query_count += 1
        raise StatusQueryError(
            ProtocolErrorCode.RECONCILIATION_UNAVAILABLE,
            "simulated status service unavailable",
        )


def test_rec_16_unavailable_worker_uses_only_bounded_read_retries(tmp_path: Path) -> None:
    fixture = create_ambiguous(tmp_path)
    writer = SingleDatabaseWriter(fixture.database_path)
    reader = StateReader(fixture.database_path)
    gate = HealthGate()
    worker = UnavailableStatusWorker()
    report = ReconciliationCoordinator(
        writer,
        reader,
        worker,
        gate,
        max_query_attempts=2,
        retry_delay=0,
    ).reconcile_all()
    writer.close()

    assert worker.query_count == 2
    assert report.results[0].outcome is ReconciliationOutcome.FAILED
    assert reader.count("reconciliation_attempts") == 2
    assert reader.one("orders", "order_id", fixture.persisted.order_id)["state"] == "UNKNOWN"


def test_rec_17_status_timeout_is_bounded_and_does_not_release_exposure(tmp_path: Path) -> None:
    fixture = create_ambiguous(tmp_path)
    report, reader = reconcile(
        fixture,
        WorkerScenario.STATUS_QUERY_TIMEOUT,
        max_query_attempts=2,
    )

    assert report.results[0].outcome is ReconciliationOutcome.FAILED
    assert reader.count("reconciliation_attempts") == 2
    assert reader.one("orders", "order_id", fixture.persisted.order_id)["state"] == "UNKNOWN"
    assert (
        reader.one("risk_reservations", "reservation_id", fixture.persisted.reservation_id)["state"]
        == "ACTIVE"
    )


def test_rec_18_23_24_three_status_queries_never_requeue_or_resubmit(tmp_path: Path) -> None:
    fixture = create_ambiguous(tmp_path)
    reader = StateReader(fixture.database_path)
    writer = SingleDatabaseWriter(fixture.database_path)
    gate = HealthGate()
    supervisor = WorkerSupervisor(
        gate,
        heartbeat_interval=10.0,
        broker_store_path=fixture.broker_store_path,
    )
    client = supervisor.start()
    try:
        query = query_from_fixture(fixture)
        results = [client.query_order_status(query, timeout=0.2) for _ in range(3)]
        attempt_id = str(uuid4())
        writer.begin_reconciliation_attempt(
            attempt_id, fixture.persisted.order_id, fixture.request.correlation_id
        )
        assert results[-1].evidence is not None
        writer.apply_reconciliation_evidence(attempt_id, results[-1].evidence)
    finally:
        supervisor.shutdown()
        writer.close()

    metrics = SimulatedBrokerStore.read_metrics(fixture.broker_store_path)
    outbox = reader.one("outbox_messages", "message_id", fixture.persisted.message_id)
    assert metrics == {
        "submit_count": 1,
        "status_query_count": 3,
        "event_delivery_count": 0,
    }
    assert outbox is not None and outbox["state"] == "RECONCILED"
    assert outbox["attempt_count"] == 1
    assert reader.list_by_state("outbox_messages", "PENDING") == []


def test_rec_19_settled_evidence_updates_pnl_and_releases_atomically(tmp_path: Path) -> None:
    fixture = create_ambiguous(
        tmp_path,
        WorkerScenario.ACCEPT_AND_SETTLE_BUT_DROP_RESPONSE,
    )
    report, reader = reconcile(fixture)

    assert report.results[0].order_state is OrderState.SETTLED
    order = reader.one("orders", "order_id", fixture.persisted.order_id)
    reservation = reader.one(
        "risk_reservations", "reservation_id", fixture.persisted.reservation_id
    )
    assert order is not None and order["realized_pnl_minor"] == 250
    assert reservation is not None and reservation["state"] == "RELEASED"


def test_rec_20_settlement_unknown_remains_active_exposure(tmp_path: Path) -> None:
    fixture = create_ambiguous(
        tmp_path,
        WorkerScenario.SETTLEMENT_UNKNOWN_BUT_DROP_RESPONSE,
    )
    report, reader = reconcile(fixture)

    assert report.results[0].order_state is OrderState.SETTLEMENT_UNKNOWN
    assert (
        reader.one("risk_reservations", "reservation_id", fixture.persisted.reservation_id)["state"]
        == "ACTIVE"
    )
    assert len(reader.list_reconciliation_candidates()) == 1


def test_rec_25_elapsed_time_alone_never_resolves_unknown(tmp_path: Path) -> None:
    fixture = create_ambiguous(tmp_path)
    writer = SingleDatabaseWriter(fixture.database_path)
    writer.cancel_expired_pending_messages(datetime.now(UTC) + timedelta(days=365))
    writer.close()
    reader = StateReader(fixture.database_path)

    assert reader.one("orders", "order_id", fixture.persisted.order_id)["state"] == "UNKNOWN"
    assert (
        reader.one("risk_reservations", "reservation_id", fixture.persisted.reservation_id)["state"]
        == "ACTIVE"
    )
    assert (
        reader.one("outbox_messages", "message_id", fixture.persisted.message_id)["state"]
        == "AMBIGUOUS"
    )


def test_migration_0003_upgrades_v2_without_losing_financial_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    connection = open_writer_connection(database_path)
    apply_migrations(connection, MIGRATIONS[:2])
    now = datetime.now(UTC).isoformat()
    connection.execute(
        """
        INSERT INTO trade_intents VALUES(
            'intent-v2', 'corr-v2', 'DERIV', 'account-v2', 'DIGITAL_OPTION',
            'EURUSD', 'CALL', 1000, 'USD', 'CREATED', ?, 'strategy-v2', '1.0.0'
        )
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO risk_reservations(
            reservation_id, intent_id, broker, account_id, amount_minor, currency,
            state, created_at
        ) VALUES ('reservation-v2', 'intent-v2', 'DERIV', 'account-v2', 1000,
                  'USD', 'ACTIVE', ?)
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO outbox_messages(
            message_id, correlation_id, intent_id, message_type, payload, state,
            created_at, available_at, attempt_count, state_reason
        ) VALUES ('message-v2', 'corr-v2', 'intent-v2', 'ORDER_SUBMIT', '{}',
                  'AMBIGUOUS', ?, ?, 1, 'POSSIBLE_SEND_TIMEOUT')
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO orders(
            order_id, intent_id, broker, account_id, state, correlation_id,
            created_at, updated_at
        ) VALUES ('order-v2', 'intent-v2', 'DERIV', 'account-v2', 'UNKNOWN',
                  'corr-v2', ?, ?)
        """,
        (now, now),
    )
    connection.close()

    writer = SingleDatabaseWriter(database_path)
    reader = StateReader(database_path)
    try:
        assert reader.count("schema_migrations") == 4
        order = reader.one("orders", "order_id", "order-v2")
        assert order is not None and order["state"] == "UNKNOWN"
        assert order["resolution_source"] is None
        assert order["last_external_sequence"] is None
        assert order["pnl_application_count"] == 0
        reservation = reader.one("risk_reservations", "reservation_id", "reservation-v2")
        assert reservation is not None and reservation["state"] == "ACTIVE"
        assert reservation["release_count"] == 0
        assert reader.count("broker_order_events") == 0
    finally:
        writer.close()


def test_migration_0004_backfills_existing_settlement_effect_counters(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    connection = open_writer_connection(database_path)
    apply_migrations(connection, MIGRATIONS[:3])
    now = datetime.now(UTC).isoformat()
    connection.execute(
        """
        INSERT INTO trade_intents VALUES(
            'intent-v3', 'corr-v3', 'DERIV', 'account-v3', 'DIGITAL_OPTION',
            'EURUSD', 'CALL', 1000, 'USD', 'CREATED', ?, 'strategy-v3', '1.0.0'
        )
        """,
        (now,),
    )
    connection.execute(
        """
        INSERT INTO risk_reservations(
            reservation_id, intent_id, broker, account_id, amount_minor, currency,
            state, created_at, released_at, release_reason
        ) VALUES ('reservation-v3', 'intent-v3', 'DERIV', 'account-v3', 1000,
                  'USD', 'RELEASED', ?, ?, 'RECONCILED_SETTLED')
        """,
        (now, now),
    )
    connection.execute(
        """
        INSERT INTO outbox_messages(
            message_id, correlation_id, intent_id, message_type, payload, state,
            created_at, available_at, dispatched_at, attempt_count, state_reason
        ) VALUES ('message-v3', 'corr-v3', 'intent-v3', 'ORDER_SUBMIT', '{}',
                  'RECONCILED', ?, ?, ?, 1, 'RECONCILED_SETTLED')
        """,
        (now, now, now),
    )
    connection.execute(
        """
        INSERT INTO orders(
            order_id, intent_id, broker, account_id, broker_order_id, state,
            correlation_id, realized_pnl_minor, created_at, updated_at,
            resolution_source, resolved_at
        ) VALUES ('order-v3', 'intent-v3', 'DERIV', 'account-v3', 'SIM-V3',
                  'SETTLED', 'corr-v3', 250, ?, ?, 'STATUS_QUERY', ?)
        """,
        (now, now, now),
    )
    connection.close()

    writer = SingleDatabaseWriter(database_path)
    reader = StateReader(database_path)
    try:
        assert reader.financial_effect_counts("order-v3") == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
        assert reader.count("schema_migrations") == 4
        assert reader.count("broker_order_events") == 0
    finally:
        writer.close()
