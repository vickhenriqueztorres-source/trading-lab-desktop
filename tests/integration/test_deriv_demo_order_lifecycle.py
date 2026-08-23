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
from apps.core.risk import RiskLedger
from apps.core.worker_client import SocketWorkerClient
from apps.deriv_worker.demo_session import DemoReadOnlyDerivSession
from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.order_session import DerivOrderSession
from apps.deriv_worker.reconciliation import DerivReconciliationHandler
from apps.deriv_worker.schema import DerivWorkerError
from apps.deriv_worker.server import DerivWorkerServer
from apps.ui.view_model import DashboardViewModel
from packages.domain.models import (
    Broker,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderCommand,
    OrderRequest,
    OrderState,
)
from packages.persistence.database import open_writer_connection
from packages.persistence.migrations import apply_migrations
from packages.persistence.reader import StateReader
from packages.persistence.writer import SingleDatabaseWriter
from packages.protocol.envelope import EndpointRole
from packages.protocol.transport import FramedSocket
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    HealthGateStatus,
    OrderSummary,
    UiAccountMode,
    UiGlobalState,
    UiProjectionSnapshot,
)


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


def test_deriv_demo_order_lifecycle_full(test_db_path: Path) -> None:
    port = _find_free_port()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)

    transport = FakeDerivTransport(
        scenario=FakeDerivScenario.BUY_SETTLE_WIN,
        demo_authenticated=True,
    )
    demo_session = DemoReadOnlyDerivSession(transport, account_id="VRTC123456")
    order_session = DerivOrderSession(transport, account_id="VRTC123456", demo_authenticated=True)
    reconciliation = DerivReconciliationHandler(transport, order_session)

    server = DerivWorkerServer(
        "127.0.0.1",
        port,
        protocol_version=1,
        session=demo_session,
        order_session=order_session,
        reconciliation_handler=reconciliation,
    )

    client_conn: list[socket.socket] = []

    def _run_server() -> None:
        conn, _ = listener.accept()
        client_conn.append(conn)
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
    assert client.capabilities.supports_reconciliation is True

    # Setup Core services
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

    # 1. Create TradeIntent & submit request to Core
    request = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITAL_OPTION",
        symbol="frxEURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        strategy_id="strat-alpha",
        strategy_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    persisted = coordinator.submit(request, dispatch=True)
    assert persisted is not None
    assert persisted.order_id is not None

    # Verify order is ACCEPTED and broker_order_id is recorded
    order_row = reader.one("orders", "order_id", persisted.order_id)
    assert order_row is not None
    assert order_row["state"] == OrderState.ACCEPTED.value
    assert order_row["broker_order_id"] is not None
    contract_id = order_row["broker_order_id"]

    # 2. Receive contract events from SocketWorkerClient
    event = client.receive_order_event(timeout=1.0)
    assert event is not None
    assert event.client_order_ref == persisted.order_id
    assert event.broker_order_id == contract_id

    # Apply event to database writer
    writer.apply_normalized_broker_event(event)

    # If first event was OPEN, receive settled event
    if event.external_status is ExternalOrderStatus.OPEN:
        order_row = reader.one("orders", "order_id", persisted.order_id)
        assert order_row is not None
        assert order_row["state"] == OrderState.OPEN.value

        event2 = client.receive_order_event(timeout=1.0)
        assert event2 is not None
        assert event2.external_status is ExternalOrderStatus.SETTLED
        writer.apply_normalized_broker_event(event2)

    # 3. Verify settled state and realized P&L on state.db
    order_row = reader.one("orders", "order_id", persisted.order_id)
    assert order_row is not None
    assert order_row["state"] == OrderState.SETTLED.value
    assert order_row["realized_pnl_minor"] == 950

    # 4. Verify UI projections reflect contract ID and P&L
    order_summaries = [
        OrderSummary(
            order_id=str(row["order_id"]),
            broker=str(row["broker"]),
            symbol=str(row["symbol"]),
            direction=str(row["direction"]),
            amount_minor_units=int(row["amount_minor"]),
            currency=str(row["currency"]),
            state=str(row["state"]),
            created_at_utc=datetime.fromisoformat(str(row["created_at"])),
            broker_order_id=str(row["broker_order_id"]) if row.get("broker_order_id") else None,
        )
        for row in reader.ui_order_summaries(limit=50)
    ]

    pnl_dict = reader.daily_realized_pnl_by_currency(
        since_utc=datetime.now(UTC).replace(hour=0, minute=0, second=0)
    )
    pnl_minor = pnl_dict.get("USD", 0)

    snapshot = UiProjectionSnapshot(
        global_state=UiGlobalState.READY,
        safe_stop_active=False,
        health_gates=(
            HealthGateStatus(
                gate_name="GLOBAL_ENTRY_GATE",
                is_open=True,
                reason_code=None,
                description="OK",
            ),
        ),
        broker_cards=(
            BrokerCardStatus(
                broker="DERIV",
                account_mode=UiAccountMode.DEMO_READ_ONLY,
                is_connected=True,
                balance_minor_units=1000000,
                currency="USD",
                clock_synced=True,
            ),
        ),
        active_orders=tuple(order_summaries),
        daily_pnl_minor_units=pnl_minor,
        daily_pnl_currency="USD",
    )
    view_model = DashboardViewModel.from_snapshot(snapshot)

    assert any(contract_id in line for line in view_model.order_lines)
    assert "9.50" in view_model.daily_pnl

    client.shutdown(timeout=1.0)


def test_deriv_demo_real_account_guardrail() -> None:
    with pytest.raises(DerivWorkerError) as exc:
        DerivOrderSession(
            FakeDerivTransport(scenario=FakeDerivScenario.NORMAL, demo_authenticated=False),
            account_id="CR999999",
            demo_authenticated=False,
        )
    assert exc.value.reason_code == "DERIV_REAL_ACCOUNT_FORBIDDEN"


def test_deriv_demo_safe_stop_blocks_new_entries(test_db_path: Path) -> None:
    writer = SingleDatabaseWriter(test_db_path)
    risk_ledger = RiskLedger()
    health_gate = HealthGate()
    serializer = AccountCommandSerializer()

    # Stub worker
    class StubWorker:
        def submit_order(self, command: OrderCommand) -> None:
            return

    # Trigger safe stop
    health_gate.block("HG_SAFE_STOP")

    coordinator = OrderCoordinator(
        writer=writer,
        worker=StubWorker(),  # type: ignore[arg-type]
        health_gate=health_gate,
        serializer=serializer,
        risk_ledger=risk_ledger,
        entry_authorizer=NullAuthPort(),
    )

    request = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITAL_OPTION",
        symbol="frxEURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        strategy_id="strat-alpha",
        strategy_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    with pytest.raises(RuntimeError, match="Health Gate blocked"):
        coordinator.submit(request)
