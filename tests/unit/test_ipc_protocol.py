from __future__ import annotations

import json
import socket
import struct
from datetime import UTC, datetime

import pytest

from packages.domain.models import (
    Broker,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderStatusQuery,
    ReconciliationEvidence,
    ReconciliationSource,
)
from packages.protocol.codec import decode_envelope, encode_envelope
from packages.protocol.envelope import EndpointRole, Envelope, MessageType
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.protocol.framing import frame_payload, receive_frame
from packages.protocol.messages import parse_order_status_request, parse_order_status_response
from packages.protocol.version import MAX_FRAME_SIZE, PROTOCOL_VERSION


def envelope() -> Envelope:
    return Envelope(
        protocol_version=PROTOCOL_VERSION,
        message_id="message-1",
        correlation_id="correlation-1",
        causation_id=None,
        source=EndpointRole.CORE,
        target=EndpointRole.SIMULATED_WORKER,
        message_type=MessageType.PING,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload={"sequence": 1},
    )


def test_ipc_03_valid_frame_and_envelope_round_trip() -> None:
    original = envelope()
    decoded = decode_envelope(encode_envelope(original))
    assert decoded == original
    assert frame_payload(b"{}") == b"\x00\x00\x00\x02{}"


def test_ipc_04_oversized_frame_rejected_from_header_without_payload_allocation() -> None:
    sender, receiver = socket.socketpair()
    try:
        sender.sendall(struct.pack("!I", MAX_FRAME_SIZE + 1))
        with pytest.raises(ProtocolError) as captured:
            receive_frame(receiver)
        assert captured.value.code is ProtocolErrorCode.IPC_FRAME_TOO_LARGE
    finally:
        sender.close()
        receiver.close()


@pytest.mark.parametrize(
    "wire",
    [
        b"\x00\x00",
        struct.pack("!I", 5) + b"{}",
    ],
)
def test_ipc_05_truncated_header_or_payload_rejected(wire: bytes) -> None:
    sender, receiver = socket.socketpair()
    try:
        sender.sendall(wire)
        sender.shutdown(socket.SHUT_WR)
        with pytest.raises(ProtocolError) as captured:
            receive_frame(receiver)
        assert captured.value.code is ProtocolErrorCode.IPC_FRAME_TRUNCATED
    finally:
        sender.close()
        receiver.close()


def test_zero_length_frame_is_rejected() -> None:
    sender, receiver = socket.socketpair()
    try:
        sender.sendall(struct.pack("!I", 0))
        with pytest.raises(ProtocolError) as captured:
            receive_frame(receiver)
        assert captured.value.code is ProtocolErrorCode.IPC_INVALID_FRAME
    finally:
        sender.close()
        receiver.close()


def test_ipc_06_invalid_json_rejected() -> None:
    with pytest.raises(ProtocolError) as captured:
        decode_envelope(b"{invalid json")
    assert captured.value.code is ProtocolErrorCode.IPC_INVALID_JSON


def test_ipc_07_missing_required_envelope_field_rejected() -> None:
    document = json.loads(encode_envelope(envelope()))
    del document["message_id"]
    with pytest.raises(ProtocolError) as captured:
        decode_envelope(json.dumps(document).encode())
    assert captured.value.code is ProtocolErrorCode.IPC_INVALID_ENVELOPE


def test_ipc_08_unknown_message_type_rejected() -> None:
    document = json.loads(encode_envelope(envelope()))
    document["message_type"] = "DELETE_ALL_MONEY"
    with pytest.raises(ProtocolError) as captured:
        decode_envelope(json.dumps(document).encode())
    assert captured.value.code is ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE


def test_role_mismatch_is_rejected_before_domain_conversion() -> None:
    document = json.loads(encode_envelope(envelope()))
    document["source"] = "RANDOM"
    document["target"] = "UI"
    with pytest.raises(ProtocolError) as captured:
        decode_envelope(json.dumps(document).encode())
    assert captured.value.code is ProtocolErrorCode.IPC_ROLE_MISMATCH


def status_query() -> OrderStatusQuery:
    return OrderStatusQuery(
        correlation_id="correlation-status",
        intent_id="intent-status",
        order_id="order-status",
        client_order_ref="order-status",
        broker=Broker.DERIV,
        account_id="practice-account",
        product="DIGITAL_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1_000, "USD"),
    )


def test_order_status_request_round_trip_preserves_financial_matching_fields() -> None:
    query = status_query()
    message = Envelope(
        protocol_version=PROTOCOL_VERSION,
        message_id="status-request",
        correlation_id=query.correlation_id,
        causation_id=None,
        source=EndpointRole.CORE,
        target=EndpointRole.SIMULATED_WORKER,
        message_type=MessageType.ORDER_STATUS_REQUEST,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload=query.to_payload(),
    )

    assert parse_order_status_request(message) == query


def test_order_status_response_converts_validated_evidence_to_immutable_domain() -> None:
    query = status_query()
    evidence = ReconciliationEvidence(
        evidence_id="evidence-status",
        source=ReconciliationSource.STATUS_QUERY,
        observed_at=datetime.now(UTC),
        client_order_ref=query.client_order_ref,
        broker_order_id="SIM-status",
        external_status=ExternalOrderStatus.ACCEPTED,
        broker=query.broker,
        account_id=query.account_id,
        product=query.product,
        symbol=query.symbol,
        direction=query.direction,
        amount=query.amount,
        evidence_version=1,
    )
    response = Envelope(
        protocol_version=PROTOCOL_VERSION,
        message_id="status-response",
        correlation_id=query.correlation_id,
        causation_id="status-request",
        source=EndpointRole.SIMULATED_WORKER,
        target=EndpointRole.CORE,
        message_type=MessageType.ORDER_STATUS_RESPONSE,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload={
            "query_outcome": "FOUND",
            "evidence": evidence.to_payload(),
            "reason_code": None,
        },
    )

    assert parse_order_status_response(response).evidence == evidence


def test_malformed_status_evidence_is_rejected_before_domain_use() -> None:
    response = Envelope(
        protocol_version=PROTOCOL_VERSION,
        message_id="status-response-invalid",
        correlation_id="correlation-status",
        causation_id="status-request",
        source=EndpointRole.SIMULATED_WORKER,
        target=EndpointRole.CORE,
        message_type=MessageType.ORDER_STATUS_RESPONSE,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload={
            "query_outcome": "FOUND",
            "evidence": {"amount_minor": "1000"},
            "reason_code": None,
        },
    )

    with pytest.raises(ProtocolError) as captured:
        parse_order_status_response(response)
    assert captured.value.code is ProtocolErrorCode.RECONCILIATION_INVALID_RESPONSE
