from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from apps.core.broker_events import BrokerEventProcessor
from apps.core.coordinator import OrderCoordinator
from apps.core.health import HealthGate
from apps.core.reconciliation import ReconciliationCoordinator, ReconciliationOutcome
from apps.core.risk import RiskLedger
from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.order_session import DerivLiveOrderSession
from apps.deriv_worker.reconciliation import DerivLiveReconciliationHandler
from packages.domain.models import Broker, Direction, Money, OrderRequest, OrderState
from packages.persistence.database import open_writer_connection
from packages.persistence.migrations import apply_migrations
from packages.persistence.reader import StateReader
from packages.persistence.writer import SingleDatabaseWriter


def test_timeout_becomes_unknown_then_statement_reconciles_without_duplicate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    connection = open_writer_connection(db_path)
    apply_migrations(connection)
    connection.close()
    writer = SingleDatabaseWriter(db_path)
    reader = StateReader(db_path)
    ledger = RiskLedger()
    gate = HealthGate()
    transport = FakeDerivTransport(FakeDerivScenario.BUY_TIMEOUT, demo_authenticated=True)
    session = DerivLiveOrderSession(transport, "VRTC123456")
    coordinator = OrderCoordinator(writer, session, gate, risk_ledger=ledger)
    request = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITAL_OPTION",
        symbol="frxEURUSD",
        direction=Direction.PUT,
        amount=Money(1000, "USD"),
        strategy_id="chaos-live-demo",
        strategy_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
    )

    persisted = coordinator.submit(request)
    unknown = reader.one("orders", "order_id", persisted.order_id)
    assert unknown is not None and unknown["state"] == OrderState.UNKNOWN.value
    assert len(reader.list_by_state("risk_reservations", "ACTIVE")) == 1

    transport.scenario = FakeDerivScenario.NORMAL
    reconciliation = ReconciliationCoordinator(
        writer,
        reader,
        DerivLiveReconciliationHandler(transport, session),
        gate,
    )
    first = reconciliation.reconcile_all()
    assert first.results[0].outcome is ReconciliationOutcome.RESOLVED
    opened = reader.one("orders", "order_id", persisted.order_id)
    assert opened is not None and opened["state"] == OrderState.OPEN.value
    assert len(reader.list_by_state("risk_reservations", "ACTIVE")) == 1

    transport.settle_latest_contract(won=False)
    session.drain_contract_events(timeout=0)
    event = session.next_queued_event(timeout=0)
    assert event is not None
    BrokerEventProcessor(writer, reader, gate, ledger).process(event)

    settled = reader.one("orders", "order_id", persisted.order_id)
    assert settled is not None and settled["state"] == OrderState.SETTLED.value
    assert settled["realized_pnl_minor"] == -1000
    counts = reader.financial_effect_counts(persisted.order_id)
    assert counts["pnl_application_count"] == 1
    assert counts["reservation_release_count"] == 1
