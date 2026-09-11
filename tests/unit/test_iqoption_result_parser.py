from __future__ import annotations

import queue
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from apps.iqoption_worker.order_session import IQOptionOrderSession
from apps.iqoption_worker.reconciliation import IQOptionReconciliationHandler
from packages.brokers.iqoption.community_read_only import IQOptionExternalError
from packages.brokers.iqoption.fake_transport import (
    FakeIQOptionScenario,
    FakeIQOptionTransport,
)
from packages.brokers.iqoption.result_parser import (
    IQOPTION_CLOSED_OPTIONS,
    IQOPTION_HISTORY_CONTAINER_KEY,
    IQOPTION_OPEN_OPTIONS,
    IQOptionResultError,
    IQOptionResultSource,
    parse_iqoption_financial_result,
)
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


class ResultTransport:
    def __init__(self, contract: Mapping[str, Any]) -> None:
        self.contract = dict(contract)

    def request(
        self,
        name: str,
        msg: Mapping[str, Any],
        *,
        timeout: float = 2.0,
    ) -> dict[str, Any]:
        assert timeout > 0
        if name == "get_betinfo":
            return {"isSuccessful": True, "result": dict(self.contract)}
        return {"isSuccessful": False, "message": "not found"}

    def receive_contract(self, *, timeout: float = 0.1) -> dict[str, Any] | None:
        return None


class UnavailableResultTransport(ResultTransport):
    def request(
        self,
        name: str,
        msg: Mapping[str, Any],
        *,
        timeout: float = 2.0,
    ) -> dict[str, Any]:
        raise IQOptionExternalError("IQOPTION_REQUEST_TIMEOUT")


class PartiallyUnavailableResultTransport(ResultTransport):
    def __init__(self) -> None:
        super().__init__({})
        self.calls = 0

    def request(
        self,
        name: str,
        msg: Mapping[str, Any],
        *,
        timeout: float = 2.0,
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return {"isSuccessful": False, "message": "not found"}
        raise IQOptionExternalError("IQOPTION_REQUEST_TIMEOUT")


class CompleteNegativeResultTransport(ResultTransport):
    def __init__(self) -> None:
        super().__init__({})
        self.last_message: dict[str, Any] | None = None

    def request(
        self,
        name: str,
        msg: Mapping[str, Any],
        *,
        timeout: float = 2.0,
    ) -> dict[str, Any]:
        assert name == "get_options"
        self.last_message = dict(msg)
        return {
            "isSuccessful": False,
            "message": "not found",
            "not_found_coverage": {
                "observed_at": datetime.now(UTC).isoformat(),
                "statement_checked": True,
                "portfolio_checked": True,
            },
        }


class FingerprintHistoryResultTransport(ResultTransport):
    def __init__(self) -> None:
        super().__init__({})

    def request(
        self,
        name: str,
        msg: Mapping[str, Any],
        *,
        timeout: float = 2.0,
    ) -> dict[str, Any]:
        assert name == "get_options"
        return {
            "isSuccessful": True,
            "result": {
                "id": "987654",
                "client_order_id": msg["client_order_id"],
                "_iq_identity_source": "HISTORY_FINGERPRINT",
                IQOPTION_HISTORY_CONTAINER_KEY: IQOPTION_CLOSED_OPTIONS,
                "active_id": 79,
                "dir": "put",
                "amount": "1.00",
                "win_amount": "1.85",
                "win": "win",
            },
        }


class RecordingOrderTransport(FakeIQOptionTransport):
    def __init__(self) -> None:
        super().__init__()
        self.buy_payload: dict[str, Any] | None = None

    def request(
        self,
        name: str,
        msg: Mapping[str, Any],
        *,
        timeout: float = 2.0,
    ) -> dict[str, Any]:
        if name == "buy":
            self.buy_payload = dict(msg)
        return super().request(name, msg, timeout=timeout)


def _query() -> OrderStatusQuery:
    return OrderStatusQuery(
        correlation_id="correlation-result",
        intent_id="intent-result",
        order_id="order-result",
        client_order_ref="order-result",
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="EURUSD-OTC",
        direction=Direction.CALL,
        amount=Money(100, "USD"),
        broker_order_id="12345",
    )


def test_reconciliation_does_not_settle_future_betinfo_with_win_hint() -> None:
    """Regression: this exact shape previously persisted SETTLED with P&L zero."""

    contract = {
        "id": 12345,
        "active": "EURUSD-OTC",
        "direction": "call",
        "currency": "USD",
        "win": "win",
        "game_state": 0,
        "profit_amount": "1.00",
        "win_amount": "1.87",
        "amount": "1.00",
        "expired": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    transport = ResultTransport(contract)
    handler = IQOptionReconciliationHandler(
        transport,
        IQOptionOrderSession(transport, practice_mode=True),
    )

    result = handler.query_order_status(_query())

    assert result.outcome is StatusQueryOutcome.FOUND
    assert result.evidence is not None
    assert result.evidence.external_status is ExternalOrderStatus.OPEN
    assert result.evidence.realized_pnl_minor is None


def test_reconciliation_timeout_is_not_reported_as_order_not_found() -> None:
    transport = UnavailableResultTransport({})
    handler = IQOptionReconciliationHandler(
        transport,
        IQOptionOrderSession(transport, practice_mode=True),
    )

    result = handler.query_order_status(_query())

    assert result.outcome is StatusQueryOutcome.UNAVAILABLE
    assert result.evidence is None
    assert result.reason_code == "IQOPTION_REQUEST_TIMEOUT"


@pytest.mark.parametrize(
    ("source", "payload"),
    [
        (
            IQOptionResultSource.BETINFO,
            {
                "game_state": 1,
                "win": "win",
                "profit": "1.87",
                "deposit": "1.00",
                # Similar names from other schemas must not override betinfo.
                "profit_amount": "1.00",
                "win_amount": "99.00",
            },
        ),
        (
            IQOptionResultSource.OPTION_CLOSED,
            {"win": "win", "win_amount": "1.87", "amount": "1.00"},
        ),
        (
            IQOptionResultSource.OPTIONS_HISTORY,
            {
                IQOPTION_HISTORY_CONTAINER_KEY: IQOPTION_CLOSED_OPTIONS,
                "win": "win",
                "win_amount": "1.87",
                "amount": "1.00",
            },
        ),
    ],
)
def test_terminal_schema_variants_use_their_authoritative_money_pair(
    source: IQOptionResultSource,
    payload: Mapping[str, Any],
) -> None:
    result = parse_iqoption_financial_result(payload, source, expected_stake_minor=100)

    assert result.external_status is ExternalOrderStatus.SETTLED
    assert result.realized_pnl_minor == 87


def test_open_history_cannot_be_settled_by_a_win_hint() -> None:
    result = parse_iqoption_financial_result(
        {
            IQOPTION_HISTORY_CONTAINER_KEY: IQOPTION_OPEN_OPTIONS,
            "win": "win",
            "win_amount": "1.87",
            "amount": "1.00",
        },
        IQOptionResultSource.OPTIONS_HISTORY,
        expected_stake_minor=100,
    )

    assert result.external_status is ExternalOrderStatus.OPEN
    assert result.realized_pnl_minor is None


def test_betinfo_with_terminal_flag_before_expiry_still_cannot_settle() -> None:
    result = parse_iqoption_financial_result(
        {
            "game_state": 1,
            "expired": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            "win": "win",
            "profit": "1.87",
            "deposit": "1.00",
        },
        IQOptionResultSource.BETINFO,
        expected_stake_minor=100,
    )

    assert result.external_status is ExternalOrderStatus.OPEN
    assert result.realized_pnl_minor is None


def test_zero_net_terminal_result_is_an_explicit_tie() -> None:
    result = parse_iqoption_financial_result(
        {"game_state": 1, "win": "equal", "profit": "1.00", "deposit": "1.00"},
        IQOptionResultSource.BETINFO,
        expected_stake_minor=100,
    )

    assert result.external_status is ExternalOrderStatus.SETTLED
    assert result.realized_pnl_minor == 0


@pytest.mark.parametrize(
    ("source", "payload"),
    [
        (IQOptionResultSource.BETINFO, {"game_state": 1, "deposit": "1.00"}),
        (IQOptionResultSource.OPTION_CLOSED, {"amount": "1.00"}),
        (
            IQOptionResultSource.OPTIONS_HISTORY,
            {IQOPTION_HISTORY_CONTAINER_KEY: IQOPTION_CLOSED_OPTIONS, "win_amount": "1.87"},
        ),
    ],
)
def test_terminal_result_missing_money_fails_closed(
    source: IQOptionResultSource,
    payload: Mapping[str, Any],
) -> None:
    with pytest.raises(IQOptionResultError, match="IQOPTION_RESULT_MONEY_MISSING"):
        parse_iqoption_financial_result(payload, source, expected_stake_minor=100)


def test_malformed_terminal_betinfo_returns_unavailable_without_evidence() -> None:
    transport = ResultTransport(
        {
            "id": 12345,
            "active": "EURUSD-OTC",
            "direction": "call",
            "currency": "USD",
            "game_state": 1,
            "win": "win",
            "deposit": "1.00",
        }
    )
    handler = IQOptionReconciliationHandler(
        transport,
        IQOptionOrderSession(transport, practice_mode=True),
    )

    result = handler.query_order_status(_query())

    assert result.outcome is StatusQueryOutcome.UNAVAILABLE
    assert result.evidence is None
    assert result.reason_code == "IQOPTION_RESULT_MONEY_MISSING"


def test_partial_reconciliation_coverage_is_unavailable_not_not_found() -> None:
    transport = PartiallyUnavailableResultTransport()
    handler = IQOptionReconciliationHandler(
        transport,
        IQOptionOrderSession(transport, practice_mode=True),
    )

    result = handler.query_order_status(_query())

    assert result.outcome is StatusQueryOutcome.UNAVAILABLE
    assert result.evidence is None
    assert result.reason_code == "IQOPTION_REQUEST_TIMEOUT"


def test_complete_iq_history_negative_result_carries_auditable_proof() -> None:
    transport = CompleteNegativeResultTransport()
    handler = IQOptionReconciliationHandler(
        transport,
        IQOptionOrderSession(transport, practice_mode=True),
    )
    query = OrderStatusQuery(
        correlation_id="correlation-negative",
        intent_id="intent-negative",
        order_id="order-negative",
        client_order_ref="order-negative",
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="EURJPY-OTC",
        direction=Direction.PUT,
        amount=Money(100, "USD"),
        submitted_at=datetime(2026, 9, 11, 9, 40, 16, tzinfo=UTC),
    )

    result = handler.query_order_status(query)

    assert result.outcome is StatusQueryOutcome.NOT_FOUND
    assert result.not_found_evidence is not None
    assert result.not_found_evidence.confirms_both_sources is True
    assert transport.last_message is not None
    assert transport.last_message["symbol"] == "EURJPY-OTC"
    assert transport.last_message["amount_minor"] == 100


def test_unique_history_fingerprint_crosses_worker_reconciliation_boundary() -> None:
    transport = FingerprintHistoryResultTransport()
    handler = IQOptionReconciliationHandler(
        transport,
        IQOptionOrderSession(transport, practice_mode=True),
    )
    query = OrderStatusQuery(
        correlation_id="correlation-fingerprint",
        intent_id="intent-fingerprint",
        order_id="order-fingerprint",
        client_order_ref="order-fingerprint",
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="EURJPY-OTC",
        direction=Direction.PUT,
        amount=Money(100, "USD"),
        submitted_at=datetime(2026, 9, 11, 9, 40, 16, tzinfo=UTC),
    )

    result = handler.query_order_status(query)

    assert result.outcome is StatusQueryOutcome.FOUND
    assert result.evidence is not None
    assert result.evidence.broker_order_id == "987654"
    assert result.evidence.external_status is ExternalOrderStatus.SETTLED
    assert result.evidence.realized_pnl_minor == 85


def test_terminal_event_queue_overflow_keeps_order_reconcilable_and_is_observable() -> None:
    transport = FakeIQOptionTransport(
        scenario=FakeIQOptionScenario.BUY_SETTLE_WIN,
        practice_mode=True,
    )
    session = IQOptionOrderSession(transport, practice_mode=True)
    command = OrderCommand(
        message_id="message-overflow",
        correlation_id="correlation-overflow",
        intent_id="intent-overflow",
        order_id="order-overflow",
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="EURUSD-OTC",
        direction=Direction.CALL,
        amount=Money(100, "USD"),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    submitted = session.submit_order(command)
    assert submitted.outcome is WorkerOutcome.ACCEPTED
    assert submitted.broker_order_id is not None
    contract_id = submitted.broker_order_id

    # Make overflow deterministic without manufacturing 1,024 unrelated events.
    session._events = queue.Queue(maxsize=1)
    opened = session.process_raw_contract_message(
        {"id": contract_id, "status": "open", "amount": "1.00"},
        result_source=IQOptionResultSource.OPTION_OPENED,
    )
    assert opened is not None

    terminal_payload = {
        "id": contract_id,
        "win": "win",
        "win_amount": "1.87",
        "amount": "1.00",
    }
    overflowed = session.process_raw_contract_message(
        terminal_payload,
        result_source=IQOptionResultSource.OPTION_CLOSED,
    )

    assert overflowed is None
    assert session.contract_events_overflow_total == 1
    assert session.reconciliation_required is True
    assert session.get_tracked_by_contract_id(contract_id) is not None
    assert session.get_tracked_by_order_id(command.order_id) is not None

    assert session.next_queued_event() is opened
    retried = session.process_raw_contract_message(
        terminal_payload,
        result_source=IQOptionResultSource.OPTION_CLOSED,
    )
    assert retried is not None
    assert session.get_tracked_by_contract_id(contract_id) is None
    assert session.get_tracked_by_order_id(command.order_id) is None


def test_order_command_preserves_exact_candle_expiry_through_worker_boundary() -> None:
    now = datetime.now(UTC)
    expiry = (now + timedelta(minutes=2)).replace(second=0, microsecond=0)
    command = OrderCommand(
        message_id="message-expiry",
        correlation_id="correlation-expiry",
        intent_id="intent-expiry",
        order_id="order-expiry",
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="EURUSD-OTC",
        direction=Direction.CALL,
        amount=Money(100, "USD"),
        deadline_at=now + timedelta(seconds=30),
        contract_expiry_at=expiry,
    )
    restored = OrderCommand.from_payload(command.to_payload())
    transport = RecordingOrderTransport()

    result = IQOptionOrderSession(transport, practice_mode=True).submit_order(restored)

    assert result.outcome is WorkerOutcome.ACCEPTED
    assert restored.contract_expiry_at == expiry
    assert transport.buy_payload is not None
    assert transport.buy_payload["expiry_epoch"] == int(expiry.timestamp())


def test_expired_entry_deadline_is_rejected_without_entering_transport() -> None:
    now = datetime.now(UTC)
    command = OrderCommand(
        message_id="message-expired",
        correlation_id="correlation-expired",
        intent_id="intent-expired",
        order_id="order-expired",
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="EURUSD-OTC",
        direction=Direction.PUT,
        amount=Money(100, "USD"),
        deadline_at=now - timedelta(milliseconds=1),
        contract_expiry_at=(now + timedelta(minutes=1)).replace(second=0, microsecond=0),
    )
    transport = RecordingOrderTransport()

    result = IQOptionOrderSession(transport, practice_mode=True).submit_order(command)

    assert result.outcome is WorkerOutcome.REJECTED
    assert result.reason_code == "IQOPTION_ENTRY_WINDOW_MISSED"
    assert transport.buy_payload is None
