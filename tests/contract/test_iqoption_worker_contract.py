from __future__ import annotations

import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from apps.core.worker_client import SocketWorkerClient
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
    ExternalOrderStatus,
    Money,
    OrderCommand,
    OrderStatusQuery,
    StatusQueryOutcome,
    WorkerOutcome,
)
from packages.protocol.envelope import EndpointRole
from packages.protocol.transport import FramedSocket


def _find_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


def _start_test_server(
    scenario: FakeIQOptionScenario = FakeIQOptionScenario.NORMAL,
) -> tuple[IQOptionWorkerServer, threading.Thread, int]:
    port = _find_free_port()
    transport = FakeIQOptionTransport(scenario=scenario, practice_mode=True)
    session = IQOptionPracticeSession(transport)
    order_session = IQOptionOrderSession(transport, practice_mode=True)
    reconciliation = IQOptionReconciliationHandler(transport, order_session)

    server = IQOptionWorkerServer(
        host="127.0.0.1",
        port=port,
        protocol_version=1,
        session=session,
        order_session=order_session,
        reconciliation_handler=reconciliation,
        scenario=scenario,
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(0.05)
    return server, thread, port


def test_iqoption_worker_handshake_and_capabilities() -> None:
    _, _, port = _start_test_server()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    framed = FramedSocket(sock)

    client = SocketWorkerClient.handshake(
        framed,
        timeout_seconds=2.0,
        expected_worker_role=EndpointRole.IQOPTION_WORKER,
        expected_broker="IQOPTION",
    )

    assert client.capabilities.broker == "IQOPTION"
    assert client.capabilities.can_submit_orders is True
    assert client.capabilities.connection_mode == "PRACTICE"
    assert client.capabilities.supports_reconciliation is True
    assert client.capabilities.supports_order_events is True

    # Query Clock
    clock = client.broker_clock()
    assert clock.server_epoch > 0

    # Query Balance
    balance = client.broker_balance()
    assert balance.currency == "USD"
    assert balance.balance_minor_units == 1000000
    assert balance.account_type == "DEMO"

    client.close()


def test_iqoption_worker_submit_order_accepted() -> None:
    _, _, port = _start_test_server(FakeIQOptionScenario.NORMAL)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    framed = FramedSocket(sock)

    client = SocketWorkerClient.handshake(
        framed,
        timeout_seconds=2.0,
        expected_worker_role=EndpointRole.IQOPTION_WORKER,
        expected_broker="IQOPTION",
    )

    cmd = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id="intent-iq-1",
        order_id="order-iq-1",
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    result = client.submit_order(cmd)
    assert result.outcome is WorkerOutcome.ACCEPTED
    assert result.broker_order_id is not None
    assert int(result.broker_order_id) >= 300_000_001

    client.close()


def test_iqoption_worker_submit_order_rejected() -> None:
    _, _, port = _start_test_server(FakeIQOptionScenario.BUY_REJECTED)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    framed = FramedSocket(sock)

    client = SocketWorkerClient.handshake(
        framed,
        timeout_seconds=2.0,
        expected_worker_role=EndpointRole.IQOPTION_WORKER,
        expected_broker="IQOPTION",
    )

    cmd = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id="intent-iq-2",
        order_id="order-iq-2",
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.PUT,
        amount=Money(1000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    result = client.submit_order(cmd)
    assert result.outcome is WorkerOutcome.REJECTED
    assert result.reason_code is not None

    client.close()


def test_iqoption_worker_submit_order_timeout() -> None:
    _, _, port = _start_test_server(FakeIQOptionScenario.BUY_TIMEOUT)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    framed = FramedSocket(sock)

    client = SocketWorkerClient.handshake(
        framed,
        timeout_seconds=2.0,
        expected_worker_role=EndpointRole.IQOPTION_WORKER,
        expected_broker="IQOPTION",
    )

    cmd = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id="intent-iq-3",
        order_id="order-iq-3",
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="GBPUSD",
        direction=Direction.CALL,
        amount=Money(2000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    result = client.submit_order(cmd)
    assert result.outcome is WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND

    client.close()


def test_iqoption_worker_reconciliation_query() -> None:
    _, _, port = _start_test_server(FakeIQOptionScenario.BUY_SETTLE_WIN)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))
    framed = FramedSocket(sock)

    client = SocketWorkerClient.handshake(
        framed,
        timeout_seconds=2.0,
        expected_worker_role=EndpointRole.IQOPTION_WORKER,
        expected_broker="IQOPTION",
    )

    cmd = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id="intent-iq-4",
        order_id="order-iq-4",
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    sub_res = client.submit_order(cmd)
    assert sub_res.outcome is WorkerOutcome.ACCEPTED
    broker_order_id = sub_res.broker_order_id

    query = OrderStatusQuery(
        correlation_id=cmd.correlation_id,
        intent_id=cmd.intent_id,
        order_id=cmd.order_id,
        client_order_ref=cmd.order_id,
        broker=Broker.IQ_OPTION,
        account_id=cmd.account_id,
        product=cmd.product,
        symbol=cmd.symbol,
        direction=cmd.direction,
        amount=cmd.amount,
        broker_order_id=broker_order_id,
    )

    res = client.query_order_status(query, timeout=1.0)
    assert res.outcome is StatusQueryOutcome.FOUND
    assert res.evidence is not None
    assert res.evidence.broker is Broker.IQ_OPTION
    assert res.evidence.external_status is ExternalOrderStatus.SETTLED
    assert res.evidence.realized_pnl_minor == 950  # 1000 * 1.95 - 1000 = 950

    client.close()
