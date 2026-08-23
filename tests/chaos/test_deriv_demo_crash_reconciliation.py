from __future__ import annotations

import socket
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from apps.core.coordinator import (
    AccountCommandSerializer,
    OrderCoordinator,
)
from apps.core.health import HealthGate
from apps.core.reconciliation import (
    ReconciliationCoordinator,
    ReconciliationOutcome,
)
from apps.core.risk import RiskLedger
from apps.core.worker_client import SocketWorkerClient
from apps.deriv_worker.demo_session import DemoReadOnlyDerivSession
from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.order_session import DerivOrderSession
from apps.deriv_worker.reconciliation import DerivReconciliationHandler
from apps.deriv_worker.server import DerivWorkerServer
from packages.domain.models import (
    Broker,
    Direction,
    Money,
    OrderRequest,
    OrderState,
)
from packages.persistence.database import open_writer_connection
from packages.persistence.migrations import apply_migrations
from packages.persistence.reader import StateReader
from packages.persistence.writer import SingleDatabaseWriter
from packages.protocol.envelope import EndpointRole
from packages.protocol.transport import FramedSocket


class NullAuthPort:
    def ensure_new_entry_allowed(
        self,
        broker: Broker,
        strategy_id: str,
        strategy_version: str,
    ) -> None:
        return


@pytest.fixture
def test_db_path(tmp_path: Path) -> Path:
    db_file = tmp_path / "state.db"
    conn = open_writer_connection(db_file)
    apply_migrations(conn)
    conn.close()
    return db_file


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_deriv_demo_crash_after_send_reconciliation(test_db_path: Path) -> None:
    port = _find_free_port()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)

    transport = FakeDerivTransport(
        scenario=FakeDerivScenario.BUY_TIMEOUT,
        demo_authenticated=True,
    )
    demo_session = DemoReadOnlyDerivSession(transport, account_id="VRTC123456")
    order_session = DerivOrderSession(transport, account_id="VRTC123456", demo_authenticated=True)
    reconciliation_handler = DerivReconciliationHandler(transport, order_session)

    server = DerivWorkerServer(
        "127.0.0.1",
        port,
        protocol_version=1,
        session=demo_session,
        order_session=order_session,
        reconciliation_handler=reconciliation_handler,
    )

    def _run_server() -> None:
        conn, _ = listener.accept()
        listener.close()
        server_framed = FramedSocket(conn)
        try:
            demo_session.connect()
            if not server._handshake(server_framed):
                return
            server._start_market_pump(server_framed)
            while True:
                req = server_framed.receive()
                server._validate_routing(req)
                server._handle(server_framed, req)
        except Exception:
            pass
        finally:
            server._stop_market_pump()
            demo_session.close()
            server_framed.close()

    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    # Connect client
    client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_sock.connect(("127.0.0.1", port))
    client_transport = FramedSocket(client_sock)

    client = SocketWorkerClient.handshake(
        client_transport,
        timeout_seconds=3.0,
        expected_worker_role=EndpointRole.DERIV_WORKER,
        expected_broker="DERIV",
    )

    assert client.capabilities.can_submit_orders is True

    writer = SingleDatabaseWriter(test_db_path)
    reader = StateReader(test_db_path)
    risk_ledger = RiskLedger()
    health_gate = HealthGate()
    serializer = AccountCommandSerializer()
    coordinator = OrderCoordinator(
        writer=writer,
        worker=client,
        health_gate=health_gate,
        serializer=serializer,
        risk_ledger=risk_ledger,
        entry_authorizer=NullAuthPort(),
    )

    # 1. Submit order with dispatch=False so we can observe state
    request = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITAL_OPTION",
        symbol="frxEURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        strategy_id="strat-chaos",
        strategy_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    persisted = coordinator.submit(request, dispatch=False)
    assert persisted is not None

    # Dispatch encounters BUY_TIMEOUT -> Order transitions to UNKNOWN
    coordinator.dispatch_pending()

    order_row = reader.one("orders", "order_id", persisted.order_id)
    assert order_row is not None
    assert order_row["state"] == OrderState.UNKNOWN.value

    # Risk reservation is preserved in UNKNOWN state
    active_reservations = reader.list_by_state("risk_reservations", "ACTIVE")
    assert len(active_reservations) == 1

    # 2. Worker transport recovers to NORMAL
    transport.scenario = FakeDerivScenario.NORMAL

    # 3. Reconciliation coordinator resolves UNKNOWN order
    reconciliation_coordinator = ReconciliationCoordinator(
        writer=writer,
        reader=reader,
        worker=client,
        health_gate=health_gate,
    )

    report = reconciliation_coordinator.reconcile_all()
    assert len(report.results) == 1
    result = report.results[0]
    assert result.outcome in (ReconciliationOutcome.RESOLVED, ReconciliationOutcome.IDEMPOTENT)

    # Verify order state after reconciliation
    order_row = reader.one("orders", "order_id", persisted.order_id)
    assert order_row is not None
    assert order_row["state"] in (
        OrderState.OPEN.value,
        OrderState.SETTLED.value,
        OrderState.REJECTED.value,
    )

    client.shutdown(timeout=1.0)
