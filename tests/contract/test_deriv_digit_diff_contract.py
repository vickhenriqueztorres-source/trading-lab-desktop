from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.order_session import DerivOrderSession
from apps.deriv_worker.request_allowlist import DerivOperation
from packages.domain.models import (
    Broker,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderCommand,
    WorkerOutcome,
)


class _RecordingTransport(FakeDerivTransport):
    def __init__(self, scenario: FakeDerivScenario) -> None:
        super().__init__(scenario, demo_authenticated=True)
        self.requests: list[tuple[DerivOperation, dict[str, object]]] = []

    def request(
        self,
        operation: DerivOperation,
        payload: Mapping[str, object],
        *,
        timeout: float,
    ) -> dict[str, object]:
        self.requests.append((operation, dict(payload)))
        return super().request(operation, payload, timeout=timeout)


def _command() -> OrderCommand:
    return OrderCommand(
        message_id="digit-message",
        correlation_id="digit-correlation",
        intent_id="digit-intent",
        order_id="digit-order",
        broker=Broker.DERIV,
        account_id="VRTC1001",
        product="DIGITDIFF",
        symbol="R_100",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        duration=1,
        duration_unit="t",
    )


@pytest.mark.parametrize(
    ("scenario", "expected_profit", "expected_exit_digit"),
    (
        (FakeDerivScenario.BUY_SETTLE_WIN, 950, 6),
        (FakeDerivScenario.BUY_SETTLE_LOSS, -1000, 5),
    ),
)
def test_digit_diff_proposal_buy_and_one_tick_settlement(
    scenario: FakeDerivScenario,
    expected_profit: int,
    expected_exit_digit: int,
) -> None:
    transport = _RecordingTransport(scenario)
    session = DerivOrderSession(transport, "VRTC1001")

    result = session.submit_digit_diff_order(_command(), 5)
    assert result.outcome is WorkerOutcome.ACCEPTED
    operations = [operation for operation, _payload in transport.requests]
    assert DerivOperation.PROPOSAL in operations
    proposal_payload = next(
        payload for operation, payload in transport.requests if operation is DerivOperation.PROPOSAL
    )
    assert proposal_payload == {
        "proposal": 1,
        "amount": Decimal("10"),
        "barrier": "5",
        "basis": "stake",
        "contract_type": "DIGITDIFF",
        "currency": "USD",
        "duration": 1,
        "duration_unit": "t",
        "underlying_symbol": "R_100",
        "passthrough": {
            "correlation_id": "digit-correlation",
            "order_id": "digit-order",
        },
    }
    buy_payload = next(
        payload for operation, payload in transport.requests if operation is DerivOperation.BUY
    )
    assert buy_payload == {
        "buy": "fake-proposal-1",
        "price": Decimal("10"),
        "passthrough": {
            "correlation_id": "digit-correlation",
            "order_id": "digit-order",
        },
    }

    session.drain_contract_events()
    events = []
    event = session.next_queued_event()
    while event is not None:
        events.append(event)
        event = session.next_queued_event()
    settled = next(item for item in events if item.external_status is ExternalOrderStatus.SETTLED)
    assert settled.product == "DIGITDIFF"
    assert settled.result_minor == expected_profit
    assert settled.current_spot is not None
    assert int(settled.current_spot[-1]) == expected_exit_digit


def test_digit_prediction_is_required_at_worker_boundary() -> None:
    transport = _RecordingTransport(FakeDerivScenario.NORMAL)
    session = DerivOrderSession(transport, "VRTC1001")
    result = session.submit_order(_command())
    assert result.outcome is WorkerOutcome.REJECTED
    assert result.reason_code == "DERIV_DIGIT_PREDICTION_REQUIRED"
    assert transport.trading_write_requests == 0
