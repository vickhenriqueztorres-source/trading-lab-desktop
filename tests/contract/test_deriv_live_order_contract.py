from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.order_session import DerivLiveOrderSession
from apps.deriv_worker.reconciliation import DerivLiveReconciliationHandler
from apps.deriv_worker.request_allowlist import DerivOperation
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


class CapturingDerivTransport(FakeDerivTransport):
    def __init__(self, scenario: FakeDerivScenario = FakeDerivScenario.NORMAL) -> None:
        super().__init__(scenario, demo_authenticated=True)
        self.payloads: list[tuple[DerivOperation, dict[str, object]]] = []

    def request(
        self,
        operation: DerivOperation,
        payload: Mapping[str, object],
        *,
        timeout: float,
    ) -> dict[str, object]:
        self.payloads.append((operation, dict(payload)))
        return super().request(operation, payload, timeout=timeout)


def _command(
    direction: Direction = Direction.CALL,
    *,
    deadline_at: datetime | None = None,
    product: str = "DIGITAL_OPTION",
    prediction_digit: int | None = None,
) -> OrderCommand:
    return OrderCommand(
        message_id=str(uuid4()),
        correlation_id=str(uuid4()),
        intent_id=str(uuid4()),
        order_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product=product,
        symbol="frxEURUSD",
        direction=direction,
        amount=Money(1234, "USD"),
        deadline_at=deadline_at or datetime.now(UTC) + timedelta(seconds=30),
        duration=1 if product == "DIGITDIFF" else 5,
        duration_unit="t" if product == "DIGITDIFF" else "s",
        prediction_digit=prediction_digit,
    )


@pytest.mark.parametrize("direction", [Direction.CALL, Direction.PUT])
def test_proposal_and_buy_payloads_preserve_decimal_and_passthrough(
    direction: Direction,
) -> None:
    transport = CapturingDerivTransport()
    session = DerivLiveOrderSession(transport, "VRTC123456")
    command = _command(direction)

    result = session.submit_buy_order(command, transport)

    assert result.outcome is WorkerOutcome.ACCEPTED
    proposal_payload = next(
        payload for operation, payload in transport.payloads if operation is DerivOperation.PROPOSAL
    )
    assert proposal_payload == {
        "proposal": 1,
        "amount": Decimal("12.34"),
        "basis": "stake",
        "contract_type": direction.value,
        "currency": "USD",
        "duration": 5,
        "duration_unit": "s",
        "underlying_symbol": "frxEURUSD",
        "passthrough": {
            "order_id": command.order_id,
            "correlation_id": command.correlation_id,
        },
    }
    buy_payload = next(
        payload for operation, payload in transport.payloads if operation is DerivOperation.BUY
    )
    assert buy_payload == {
        "buy": "fake-proposal-1",
        "price": Decimal("12.34"),
        "passthrough": {
            "order_id": command.order_id,
            "correlation_id": command.correlation_id,
        },
    }


def test_expired_command_is_rejected_before_socket_send() -> None:
    transport = CapturingDerivTransport()
    session = DerivLiveOrderSession(transport, "VRTC123456")

    result = session.submit_order(_command(deadline_at=datetime.now(UTC) - timedelta(seconds=1)))

    assert result.outcome is WorkerOutcome.REJECTED
    assert result.reason_code == "ORDER_COMMAND_EXPIRED"
    assert all(operation is not DerivOperation.BUY for operation, _payload in transport.payloads)


def test_open_contract_stream_normalizes_settlement_and_forgets_subscription() -> None:
    transport = CapturingDerivTransport(FakeDerivScenario.BUY_SETTLE_WIN)
    session = DerivLiveOrderSession(transport, "VRTC123456")
    result = session.submit_order(_command())
    assert result.broker_order_id is not None

    raw_open = transport.receive_contract(timeout=0)
    raw_settled = transport.receive_contract(timeout=0)
    assert raw_open is not None and raw_settled is not None

    opened = session.on_proposal_open_contract_message(raw_open)
    settled = session.on_proposal_open_contract_message(raw_settled)

    assert opened is not None and opened.external_status is ExternalOrderStatus.OPEN
    assert opened.seconds_remaining is not None
    assert settled is not None and settled.external_status is ExternalOrderStatus.SETTLED
    assert settled.result_minor == 1172
    assert settled.evidence_hash == settled.expected_evidence_hash()
    assert transport.operation_counts[DerivOperation.FORGET] == 1


def test_reconciliation_rejects_amount_mismatch() -> None:
    transport = CapturingDerivTransport()
    session = DerivLiveOrderSession(transport, "VRTC123456")
    command = _command()
    submitted = session.submit_order(command)
    assert submitted.broker_order_id is not None
    query_payload: dict[str, Any] = {
        **command.to_payload(),
        "client_order_ref": command.order_id,
        "broker_order_id": submitted.broker_order_id,
        "amount_minor": 999,
    }
    query = OrderStatusQuery.from_payload(query_payload, command.correlation_id)

    result = DerivLiveReconciliationHandler(transport, session).query_order_status(query)

    assert result.outcome is StatusQueryOutcome.UNAVAILABLE
    assert result.reason_code == "DERIV_RECONCILIATION_AMOUNT_MISMATCH"


def test_reconciliation_accepts_digitdiff_contract_type_for_call_command() -> None:
    transport = CapturingDerivTransport()
    session = DerivLiveOrderSession(transport, "VRTC123456")
    command = _command(product="DIGITDIFF", prediction_digit=4)
    submitted = session.submit_order(command)
    assert submitted.broker_order_id is not None
    query = OrderStatusQuery.from_payload(
        {
            **command.to_payload(),
            "client_order_ref": command.order_id,
            "broker_order_id": submitted.broker_order_id,
        },
        command.correlation_id,
    )

    result = DerivLiveReconciliationHandler(transport, session).query_order_status(query)

    assert result.outcome is StatusQueryOutcome.FOUND
    assert result.evidence is not None
