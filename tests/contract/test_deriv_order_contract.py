from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from apps.deriv_worker.demo_session import DemoReadOnlyDerivSession
from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.order_session import DerivOrderSession
from apps.deriv_worker.reconciliation import DerivReconciliationHandler
from apps.deriv_worker.request_allowlist import DerivOperation
from apps.deriv_worker.schema import DerivWorkerError
from apps.deriv_worker.server import DerivWorkerServer
from packages.domain.models import (
    Broker,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderCommand,
    OrderStatusQuery,
    ReconciliationSource,
    StatusQueryOutcome,
    WorkerOutcome,
)
from packages.protocol.envelope import EndpointRole, Envelope, MessageType


def test_deriv_order_session_submit_success() -> None:
    transport = FakeDerivTransport(scenario=FakeDerivScenario.NORMAL, demo_authenticated=True)
    session = DerivOrderSession(transport, account_id="VRTC123456", demo_authenticated=True)

    command = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id="intent-001",
        order_id="order-deriv-001",
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITAL_OPTION",
        symbol="frxEURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    result = session.submit_order(command)
    assert result.outcome is WorkerOutcome.ACCEPTED
    assert result.broker_order_id is not None
    assert result.correlation_id == command.correlation_id


def test_deriv_order_session_submit_rejected() -> None:
    transport = FakeDerivTransport(scenario=FakeDerivScenario.BUY_REJECTED, demo_authenticated=True)
    session = DerivOrderSession(transport, account_id="VRTC123456", demo_authenticated=True)

    command = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id="intent-002",
        order_id="order-deriv-002",
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITAL_OPTION",
        symbol="frxEURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    result = session.submit_order(command)
    assert result.outcome is WorkerOutcome.REJECTED
    assert result.broker_order_id is None
    assert result.reason_code == "MarketClosed"


def test_deriv_order_session_submit_timeout() -> None:
    transport = FakeDerivTransport(scenario=FakeDerivScenario.BUY_TIMEOUT, demo_authenticated=True)
    session = DerivOrderSession(transport, account_id="VRTC123456", demo_authenticated=True)

    command = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id="intent-003",
        order_id="order-deriv-003",
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITAL_OPTION",
        symbol="frxEURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    result = session.submit_order(command)
    assert result.outcome is WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND
    assert result.broker_order_id is None
    assert result.reason_code == "DERIV_REQUEST_TIMEOUT"


def test_deriv_order_session_real_account_forbidden() -> None:
    transport = FakeDerivTransport(scenario=FakeDerivScenario.NORMAL, demo_authenticated=False)
    with pytest.raises(DerivWorkerError) as exc:
        DerivOrderSession(transport, account_id="CR123456", demo_authenticated=False)
    assert exc.value.reason_code == "DERIV_REAL_ACCOUNT_FORBIDDEN"


def test_deriv_order_session_contract_event_streaming() -> None:
    transport = FakeDerivTransport(
        scenario=FakeDerivScenario.BUY_SETTLE_WIN, demo_authenticated=True
    )
    session = DerivOrderSession(transport, account_id="VRTC123456", demo_authenticated=True)

    command = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id="intent-004",
        order_id="order-deriv-004",
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITAL_OPTION",
        symbol="frxEURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    result = session.submit_order(command)
    assert result.outcome is WorkerOutcome.ACCEPTED

    # Drain events
    drained = session.drain_contract_events(timeout=0.01)
    assert drained >= 1

    event1 = session.next_queued_event(timeout=0.0)
    assert event1 is not None
    assert event1.client_order_ref == "order-deriv-004"
    assert event1.broker == Broker.DERIV
    assert event1.external_status in (ExternalOrderStatus.OPEN, ExternalOrderStatus.SETTLED)


def test_deriv_reconciliation_handler_found() -> None:
    transport = FakeDerivTransport(
        scenario=FakeDerivScenario.BUY_SETTLE_WIN, demo_authenticated=True
    )
    session = DerivOrderSession(transport, account_id="VRTC123456", demo_authenticated=True)
    reconciliation = DerivReconciliationHandler(transport, session)

    command = OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id="intent-005",
        order_id="order-deriv-005",
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITAL_OPTION",
        symbol="frxEURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    result = session.submit_order(command)
    assert result.outcome is WorkerOutcome.ACCEPTED
    broker_order_id = result.broker_order_id
    assert broker_order_id is not None

    query = OrderStatusQuery(
        correlation_id=command.correlation_id,
        intent_id="intent-005",
        order_id="order-deriv-005",
        client_order_ref="order-deriv-005",
        broker_order_id=broker_order_id,
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITAL_OPTION",
        symbol="frxEURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
    )

    res = reconciliation.query_order_status(query)
    assert res.outcome is StatusQueryOutcome.FOUND
    assert res.evidence is not None
    assert res.evidence.source is ReconciliationSource.STATUS_QUERY
    assert res.evidence.broker_order_id == broker_order_id
    assert res.evidence.external_status in (ExternalOrderStatus.OPEN, ExternalOrderStatus.SETTLED)


class _NoPassthroughProfitTransport(FakeDerivTransport):
    def __init__(self, *, duplicate_match: bool = False) -> None:
        super().__init__(demo_authenticated=True)
        self._duplicate_match = duplicate_match

    def request(
        self,
        operation: DerivOperation,
        payload: Mapping[str, object],
        *,
        timeout: float,
    ) -> dict[str, object]:
        del timeout
        if operation is DerivOperation.STATEMENT:
            return {"statement": {"transactions": []}}
        if operation is DerivOperation.PROFIT_TABLE:
            records: list[dict[str, object]] = [
                {
                    "buy_price": 1,
                    "contract_id": 7001,
                    "contract_type": "DIGITDIFF",
                    "duration_type": "ticks",
                    "purchase_time": 1_800_000_001,
                    "underlying_symbol": "R_100",
                }
            ]
            if self._duplicate_match:
                records.append({**records[0], "contract_id": 7002})
            return {"profit_table": {"transactions": records}}
        if operation is DerivOperation.PROPOSAL_OPEN_CONTRACT:
            contract_id = int(str(payload["contract_id"]))
            return {
                "proposal_open_contract": {
                    "contract_id": contract_id,
                    "underlying": "R_100",
                    "contract_type": "DIGITDIFF",
                    "currency": "USD",
                    "buy_price": "1.00",
                    "profit": "0.09",
                    "status": "won",
                    "is_sold": 1,
                    "is_expired": 1,
                }
            }
        return super().request(operation, payload, timeout=3.0)


def _ambiguous_digit_query() -> OrderStatusQuery:
    return OrderStatusQuery(
        correlation_id="corr-ambiguous-digit",
        intent_id="intent-ambiguous-digit",
        order_id="order-ambiguous-digit",
        client_order_ref="order-ambiguous-digit",
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITDIFF",
        symbol="R_100",
        direction=Direction.CALL,
        amount=Money(100, "USD"),
        submitted_at=datetime.fromtimestamp(1_800_000_000, UTC),
    )


def test_deriv_reconciliation_uniquely_matches_profit_table_without_passthrough() -> None:
    transport = _NoPassthroughProfitTransport()
    session = DerivOrderSession(transport, account_id="VRTC123456", demo_authenticated=True)

    result = DerivReconciliationHandler(transport, session).query_order_status(
        _ambiguous_digit_query()
    )

    assert result.outcome is StatusQueryOutcome.FOUND
    assert result.evidence is not None
    assert result.evidence.broker_order_id == "7001"
    assert result.evidence.realized_pnl_minor == 9


def test_deriv_reconciliation_refuses_non_unique_profit_table_match() -> None:
    transport = _NoPassthroughProfitTransport(duplicate_match=True)
    session = DerivOrderSession(transport, account_id="VRTC123456", demo_authenticated=True)

    result = DerivReconciliationHandler(transport, session).query_order_status(
        _ambiguous_digit_query()
    )

    assert result.outcome is StatusQueryOutcome.NOT_FOUND
    assert result.reason_code == "DERIV_RECONCILIATION_AMBIGUOUS_MATCH"


def test_deriv_worker_server_demo_capabilities_and_order_dispatch() -> None:
    transport = FakeDerivTransport(scenario=FakeDerivScenario.NORMAL, demo_authenticated=True)
    demo_session = DemoReadOnlyDerivSession(transport, account_id="VRTC123456")
    order_session = DerivOrderSession(transport, account_id="VRTC123456", demo_authenticated=True)
    server = DerivWorkerServer(
        "127.0.0.1",
        1,
        protocol_version=1,
        session=demo_session,
        order_session=order_session,
    )

    assert server._capabilities.can_submit_orders is True
    assert server._capabilities.supports_order_status_query is True
    assert server._capabilities.supports_reconciliation is True

    deadline = datetime.now(UTC) + timedelta(minutes=1)
    # Test IPC order submit
    command_payload = {
        "message_id": str(uuid4()),
        "correlation_id": str(uuid4()),
        "intent_id": "intent-ipc-001",
        "order_id": "order-ipc-001",
        "broker": "DERIV",
        "account_id": "VRTC123456",
        "product": "DIGITAL_OPTION",
        "symbol": "frxEURUSD",
        "direction": "CALL",
        "amount_minor": 1000,
        "currency": "USD",
        "deadline_at": deadline.isoformat(),
    }
    request = Envelope(
        protocol_version=1,
        message_id=command_payload["message_id"],
        correlation_id=command_payload["correlation_id"],
        causation_id=None,
        source=EndpointRole.CORE,
        target=EndpointRole.DERIV_WORKER,
        message_type=MessageType.ORDER_SUBMIT,
        created_at_utc=datetime.now(UTC),
        deadline_at=deadline,
        payload=command_payload,
    )

    msg_type, reply_payload = server._dispatch_read_only(request)
    assert msg_type is MessageType.ORDER_ACCEPTED
    assert reply_payload["order_id"] == "order-ipc-001"
    assert reply_payload["broker_order_id"] is not None
