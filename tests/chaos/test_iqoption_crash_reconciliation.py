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
from apps.core.reconciliation import ReconciliationCoordinator, ReconciliationOutcome
from apps.core.risk import RiskLedger
from apps.core.worker_client import SocketWorkerClient
from apps.deriv_worker.demo_session import DemoReadOnlyDerivSession
from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.order_session import DerivOrderSession
from apps.deriv_worker.server import DerivWorkerServer
from apps.iqoption_worker.order_session import IQOptionOrderSession
from apps.iqoption_worker.reconciliation import IQOptionReconciliationHandler
from apps.iqoption_worker.server import IQOptionWorkerServer
from packages.brokers.iqoption.fake_transport import (
    FakeIQOptionScenario,
    FakeIQOptionTransport,
)
from packages.brokers.iqoption.session import IQOptionPracticeSession
from packages.domain.models import (
    Broker,
    Direction,
    Money,
    OrderRequest,
    OrderState,
    RiskReservationState,
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


def test_iqoption_crash_and_reconciliation_recovery(test_db_path: Path) -> None:
    port = _find_free_port()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)

    transport = FakeIQOptionTransport(
        scenario=FakeIQOptionScenario.BUY_TIMEOUT,
        practice_mode=True,
    )
    session = IQOptionPracticeSession(transport)
    order_session = IQOptionOrderSession(transport, practice_mode=True)
    reconciliation = IQOptionReconciliationHandler(transport, order_session)

    server = IQOptionWorkerServer(
        "127.0.0.1",
        port,
        protocol_version=1,
        session=session,
        order_session=order_session,
        reconciliation_handler=reconciliation,
        scenario=FakeIQOptionScenario.BUY_TIMEOUT,
    )

    def _run_server() -> None:
        conn, _ = listener.accept()
        listener.close()
        server_framed = FramedSocket(conn)
        try:
            session.connect()
            if not server._handshake(server_framed):
                return
            while True:
                req = server_framed.receive()
                server._validate_routing(req)
                server._handle(server_framed, req)
        except Exception:
            pass
        finally:
            session.close()
            server_framed.close()

    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_sock.connect(("127.0.0.1", port))
    client_transport = FramedSocket(client_sock)

    client = SocketWorkerClient.handshake(
        client_transport,
        timeout_seconds=3.0,
        expected_worker_role=EndpointRole.IQOPTION_WORKER,
        expected_broker="IQOPTION",
    )

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

    request = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        strategy_id="strat-iq-chaos",
        strategy_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    # Submit order - will hit BUY_TIMEOUT in fake transport
    persisted = coordinator.submit(request, dispatch=True)
    assert persisted is not None

    # Invariant check: order state is UNKNOWN, risk reservation is preserved ACTIVE
    order_row = reader.one("orders", "order_id", persisted.order_id)
    assert order_row is not None
    assert order_row["state"] == OrderState.UNKNOWN.value

    res_row = reader.one("risk_reservations", "reservation_id", persisted.reservation_id)
    assert res_row is not None
    assert res_row["state"] == RiskReservationState.ACTIVE.value

    # Now recover transport to normal scenario for reconciliation
    transport.scenario = FakeIQOptionScenario.BUY_SETTLE_WIN

    # Run authoritative reconciliation coordinator
    reconciler = ReconciliationCoordinator(
        writer=writer,
        reader=reader,
        worker=client,
        health_gate=health_gate,
    )

    report = reconciler.reconcile_all()
    assert len(report.results) == 1
    result = report.results[0]
    assert result.outcome in (ReconciliationOutcome.RESOLVED, ReconciliationOutcome.IDEMPOTENT)

    # Order must now be reconciled to a conclusive state
    reconciled_order = reader.one("orders", "order_id", persisted.order_id)
    assert reconciled_order is not None
    assert reconciled_order["state"] in (
        OrderState.OPEN.value,
        OrderState.SETTLED.value,
        OrderState.REJECTED.value,
    )

    client.close()


def test_iqoption_worker_crash_isolation_from_deriv() -> None:
    # 1. Start Deriv server
    deriv_port = _find_free_port()
    deriv_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    deriv_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    deriv_listener.bind(("127.0.0.1", deriv_port))
    deriv_listener.listen(1)

    deriv_transport = FakeDerivTransport(
        scenario=FakeDerivScenario.BUY_SETTLE_WIN,
        demo_authenticated=True,
    )
    deriv_session = DemoReadOnlyDerivSession(deriv_transport, account_id="VRTC123")
    deriv_order_session = DerivOrderSession(
        deriv_transport,
        account_id="VRTC123",
        demo_authenticated=True,
    )
    deriv_server = DerivWorkerServer(
        "127.0.0.1",
        deriv_port,
        protocol_version=1,
        session=deriv_session,
        order_session=deriv_order_session,
    )

    def _run_deriv_server() -> None:
        conn, _ = deriv_listener.accept()
        deriv_listener.close()
        framed = FramedSocket(conn)
        try:
            deriv_session.connect()
            if not deriv_server._handshake(framed):
                return
            while True:
                req = framed.receive()
                deriv_server._validate_routing(req)
                deriv_server._handle(framed, req)
        except Exception:
            pass
        finally:
            deriv_session.close()
            framed.close()

    deriv_thread = threading.Thread(target=_run_deriv_server, daemon=True)
    deriv_thread.start()

    # 2. Start IQ Option server
    iq_port = _find_free_port()
    iq_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    iq_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    iq_listener.bind(("127.0.0.1", iq_port))
    iq_listener.listen(1)

    iq_transport = FakeIQOptionTransport(
        scenario=FakeIQOptionScenario.NORMAL,
        practice_mode=True,
    )
    iq_session = IQOptionPracticeSession(iq_transport)
    iq_order_session = IQOptionOrderSession(iq_transport, practice_mode=True)
    iq_server = IQOptionWorkerServer(
        "127.0.0.1",
        iq_port,
        protocol_version=1,
        session=iq_session,
        order_session=iq_order_session,
    )

    iq_conn_holder: list[socket.socket] = []

    def _run_iq_server() -> None:
        conn, _ = iq_listener.accept()
        iq_conn_holder.append(conn)
        iq_listener.close()
        framed = FramedSocket(conn)
        try:
            iq_session.connect()
            if not iq_server._handshake(framed):
                return
            while True:
                req = framed.receive()
                iq_server._validate_routing(req)
                iq_server._handle(framed, req)
        except Exception:
            pass
        finally:
            iq_session.close()
            framed.close()

    iq_thread = threading.Thread(target=_run_iq_server, daemon=True)
    iq_thread.start()

    # 3. Connect both clients
    deriv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    deriv_sock.connect(("127.0.0.1", deriv_port))
    deriv_client = SocketWorkerClient.handshake(
        FramedSocket(deriv_sock),
        timeout_seconds=3.0,
        expected_worker_role=EndpointRole.DERIV_WORKER,
        expected_broker="DERIV",
    )

    iq_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    iq_sock.connect(("127.0.0.1", iq_port))
    _iq_client = SocketWorkerClient.handshake(
        FramedSocket(iq_sock),
        timeout_seconds=3.0,
        expected_worker_role=EndpointRole.IQOPTION_WORKER,
        expected_broker="IQOPTION",
    )

    # 4. Crash IQ Option connection
    if iq_conn_holder:
        iq_conn_holder[0].close()
    iq_sock.close()

    # 5. Verify Deriv continues operating normally
    clock = deriv_client.broker_clock()
    assert clock.server_epoch > 0

    deriv_client.close()
