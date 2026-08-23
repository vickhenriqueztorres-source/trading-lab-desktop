from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from packages.protocol.envelope import EndpointRole, Envelope, MessageType
from packages.protocol.errors import ProtocolError, ProtocolErrorCode

_ENVELOPE_FIELDS = {
    "protocol_version",
    "message_id",
    "correlation_id",
    "causation_id",
    "source",
    "target",
    "message_type",
    "created_at_utc",
    "deadline_at",
    "payload",
}


def _json_compatible(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_compatible(item) for item in value]
    return value


def encode_envelope(envelope: Envelope) -> bytes:
    document = {
        "protocol_version": envelope.protocol_version,
        "message_id": envelope.message_id,
        "correlation_id": envelope.correlation_id,
        "causation_id": envelope.causation_id,
        "source": envelope.source.value,
        "target": envelope.target.value,
        "message_type": envelope.message_type.value,
        "created_at_utc": envelope.created_at_utc.isoformat(),
        "deadline_at": envelope.deadline_at.isoformat() if envelope.deadline_at else None,
        "payload": _json_compatible(envelope.payload),
    }
    try:
        return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "envelope contains a non-JSON value",
        ) from exc


def _require_type(document: dict[str, Any], field: str, expected: type[Any]) -> Any:
    value = document.get(field)
    if isinstance(value, bool) or not isinstance(value, expected):
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            f"invalid or missing envelope field: {field}",
        )
    return value


def decode_envelope(payload: bytes) -> Envelope:
    try:
        decoded = payload.decode("utf-8")
        document = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(ProtocolErrorCode.IPC_INVALID_JSON, "invalid JSON frame") from exc
    if not isinstance(document, dict) or set(document) != _ENVELOPE_FIELDS:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "envelope fields do not match IPC v1",
        )
    version = _require_type(document, "protocol_version", int)
    message_id = _require_type(document, "message_id", str)
    correlation_id = _require_type(document, "correlation_id", str)
    source_value = _require_type(document, "source", str)
    target_value = _require_type(document, "target", str)
    message_type_value = _require_type(document, "message_type", str)
    created_value = _require_type(document, "created_at_utc", str)
    causation_id = document["causation_id"]
    deadline_value = document["deadline_at"]
    raw_payload = document["payload"]
    if causation_id is not None and not isinstance(causation_id, str):
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "causation_id must be a string or null",
        )
    if deadline_value is not None and not isinstance(deadline_value, str):
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "deadline_at must be a timestamp or null",
        )
    if not isinstance(raw_payload, dict):
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "payload must be an object",
        )
    try:
        source = EndpointRole(source_value)
        target = EndpointRole(target_value)
    except ValueError as exc:
        raise ProtocolError(ProtocolErrorCode.IPC_ROLE_MISMATCH, "unknown IPC role") from exc
    try:
        message_type = MessageType(message_type_value)
    except ValueError as exc:
        raise ProtocolError(
            ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
            "unknown IPC message type",
        ) from exc
    try:
        created_at = datetime.fromisoformat(created_value)
        deadline_at = datetime.fromisoformat(deadline_value) if deadline_value else None
    except ValueError as exc:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "invalid envelope timestamp",
        ) from exc
    return Envelope(
        protocol_version=version,
        message_id=message_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        source=source,
        target=target,
        message_type=message_type,
        created_at_utc=created_at,
        deadline_at=deadline_at,
        payload=raw_payload,
    )
