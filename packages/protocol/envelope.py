from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from packages.protocol.errors import ProtocolError, ProtocolErrorCode


class EndpointRole(StrEnum):
    LAUNCHER = "LAUNCHER"
    UI = "UI"
    CORE = "CORE"
    AUTH_AGENT = "AUTH_AGENT"
    SIMULATED_WORKER = "SIMULATED_WORKER"
    DERIV_WORKER = "DERIV_WORKER"
    IQOPTION_WORKER = "IQOPTION_WORKER"


class MessageType(StrEnum):
    UI_HANDSHAKE_REQUEST = "UI_HANDSHAKE_REQUEST"
    UI_HANDSHAKE_RESPONSE = "UI_HANDSHAKE_RESPONSE"
    UI_PROJECTION_REQUEST = "UI_PROJECTION_REQUEST"
    UI_PROJECTION_SNAPSHOT = "UI_PROJECTION_SNAPSHOT"
    UI_SAFE_STOP_COMMAND = "UI_SAFE_STOP_COMMAND"
    UI_SAFE_STOP_ACK = "UI_SAFE_STOP_ACK"
    UI_RESUME_COMMAND = "UI_RESUME_COMMAND"
    UI_RESUME_ACK = "UI_RESUME_ACK"
    UI_SHUTDOWN_REQUEST = "UI_SHUTDOWN_REQUEST"
    UI_SHUTDOWN_ACK = "UI_SHUTDOWN_ACK"
    UI_GENERATE_DIAGNOSTIC_COMMAND = "UI_GENERATE_DIAGNOSTIC_COMMAND"
    UI_GENERATE_DIAGNOSTIC_RESPONSE = "UI_GENERATE_DIAGNOSTIC_RESPONSE"
    UI_DERIV_DEMO_CONNECT_COMMAND = "UI_DERIV_DEMO_CONNECT_COMMAND"
    UI_DERIV_DEMO_CONNECT_ACK = "UI_DERIV_DEMO_CONNECT_ACK"
    UI_UPDATE_DIGIT_RISK_CONFIG_COMMAND = "UI_UPDATE_DIGIT_RISK_CONFIG_COMMAND"
    UI_UPDATE_DIGIT_RISK_CONFIG_ACK = "UI_UPDATE_DIGIT_RISK_CONFIG_ACK"
    UI_RESET_DIGIT_TEST_SESSION_COMMAND = "UI_RESET_DIGIT_TEST_SESSION_COMMAND"
    UI_RESET_DIGIT_TEST_SESSION_ACK = "UI_RESET_DIGIT_TEST_SESSION_ACK"
    LIFECYCLE_HANDSHAKE_REQUEST = "LIFECYCLE_HANDSHAKE_REQUEST"
    LIFECYCLE_HANDSHAKE_RESPONSE = "LIFECYCLE_HANDSHAKE_RESPONSE"
    CORE_LIFECYCLE_STATUS_REQUEST = "CORE_LIFECYCLE_STATUS_REQUEST"
    CORE_LIFECYCLE_STATUS_RESPONSE = "CORE_LIFECYCLE_STATUS_RESPONSE"
    CORE_SAFE_STOP_REQUEST = "CORE_SAFE_STOP_REQUEST"
    CORE_SAFE_STOP_ACK = "CORE_SAFE_STOP_ACK"
    CORE_DRAIN_REQUEST = "CORE_DRAIN_REQUEST"
    CORE_DRAIN_RESPONSE = "CORE_DRAIN_RESPONSE"
    CORE_WORKERS_SHUTDOWN_REQUEST = "CORE_WORKERS_SHUTDOWN_REQUEST"
    CORE_WORKERS_SHUTDOWN_ACK = "CORE_WORKERS_SHUTDOWN_ACK"
    CORE_AUTH_SHUTDOWN_REQUEST = "CORE_AUTH_SHUTDOWN_REQUEST"
    CORE_AUTH_SHUTDOWN_ACK = "CORE_AUTH_SHUTDOWN_ACK"
    CORE_RESTART_COMPONENT_REQUEST = "CORE_RESTART_COMPONENT_REQUEST"
    CORE_RESTART_COMPONENT_RESPONSE = "CORE_RESTART_COMPONENT_RESPONSE"
    CORE_PROCESS_SHUTDOWN_REQUEST = "CORE_PROCESS_SHUTDOWN_REQUEST"
    CORE_PROCESS_SHUTDOWN_ACK = "CORE_PROCESS_SHUTDOWN_ACK"
    AUTH_HANDSHAKE_REQUEST = "AUTH_HANDSHAKE_REQUEST"
    AUTH_HANDSHAKE_RESPONSE = "AUTH_HANDSHAKE_RESPONSE"
    AUTH_START_LOGIN_REQUEST = "AUTH_START_LOGIN_REQUEST"
    AUTH_START_LOGIN_RESPONSE = "AUTH_START_LOGIN_RESPONSE"
    AUTH_SUBMIT_OTP_REQUEST = "AUTH_SUBMIT_OTP_REQUEST"
    AUTH_SUBMIT_OTP_RESPONSE = "AUTH_SUBMIT_OTP_RESPONSE"
    AUTH_RENEW_REQUEST = "AUTH_RENEW_REQUEST"
    AUTH_RENEW_RESPONSE = "AUTH_RENEW_RESPONSE"
    AUTH_CHECK_AUTHORIZATION_REQUEST = "AUTH_CHECK_AUTHORIZATION_REQUEST"
    AUTH_CHECK_AUTHORIZATION_RESPONSE = "AUTH_CHECK_AUTHORIZATION_RESPONSE"
    AUTH_STATUS_REQUEST = "AUTH_STATUS_REQUEST"
    AUTH_STATUS_RESPONSE = "AUTH_STATUS_RESPONSE"
    AUTH_SHUTDOWN_REQUEST = "AUTH_SHUTDOWN_REQUEST"
    AUTH_SHUTDOWN_ACK = "AUTH_SHUTDOWN_ACK"
    HELLO = "HELLO"
    HELLO_ACK = "HELLO_ACK"
    PING = "PING"
    PONG = "PONG"
    ORDER_SUBMIT = "ORDER_SUBMIT"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_STATUS_UNKNOWN = "ORDER_STATUS_UNKNOWN"
    ORDER_STATUS_REQUEST = "ORDER_STATUS_REQUEST"
    ORDER_STATUS_RESPONSE = "ORDER_STATUS_RESPONSE"
    ORDER_EVENT = "ORDER_EVENT"
    BROKER_CAPABILITIES_REQUEST = "BROKER_CAPABILITIES_REQUEST"
    BROKER_CAPABILITIES_RESPONSE = "BROKER_CAPABILITIES_RESPONSE"
    MARKET_SYMBOLS_REQUEST = "MARKET_SYMBOLS_REQUEST"
    MARKET_SYMBOLS_RESPONSE = "MARKET_SYMBOLS_RESPONSE"
    MARKET_CONTRACTS_REQUEST = "MARKET_CONTRACTS_REQUEST"
    MARKET_CONTRACTS_RESPONSE = "MARKET_CONTRACTS_RESPONSE"
    MARKET_TICK_SUBSCRIBE = "MARKET_TICK_SUBSCRIBE"
    MARKET_TICK_SUBSCRIBED = "MARKET_TICK_SUBSCRIBED"
    MARKET_TICK_EVENT = "MARKET_TICK_EVENT"
    MARKET_TICK_UNSUBSCRIBE = "MARKET_TICK_UNSUBSCRIBE"
    MARKET_TICK_UNSUBSCRIBED = "MARKET_TICK_UNSUBSCRIBED"
    MARKET_HISTORY_REQUEST = "MARKET_HISTORY_REQUEST"
    MARKET_HISTORY_RESPONSE = "MARKET_HISTORY_RESPONSE"
    BROKER_CLOCK_REQUEST = "BROKER_CLOCK_REQUEST"
    BROKER_CLOCK_RESPONSE = "BROKER_CLOCK_RESPONSE"
    BROKER_BALANCE_REQUEST = "BROKER_BALANCE_REQUEST"
    BROKER_BALANCE_RESPONSE = "BROKER_BALANCE_RESPONSE"
    BROKER_QUOTE_REQUEST = "BROKER_QUOTE_REQUEST"
    BROKER_QUOTE_RESPONSE = "BROKER_QUOTE_RESPONSE"
    WORKER_HEALTH_REQUEST = "WORKER_HEALTH_REQUEST"
    WORKER_HEALTH_RESPONSE = "WORKER_HEALTH_RESPONSE"
    SHUTDOWN = "SHUTDOWN"
    SHUTDOWN_ACK = "SHUTDOWN_ACK"
    ERROR = "ERROR"


def _validate_utc(value: datetime, field: str) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            f"{field} must be timezone-aware UTC",
        )


@dataclass(frozen=True, slots=True, repr=False)
class Envelope:
    protocol_version: int
    message_id: str
    correlation_id: str
    causation_id: str | None
    source: EndpointRole
    target: EndpointRole
    message_type: MessageType
    created_at_utc: datetime
    deadline_at: datetime | None
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if isinstance(self.protocol_version, bool) or self.protocol_version <= 0:
            raise ProtocolError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                "protocol_version must be a positive integer",
            )
        for field_name in ("message_id", "correlation_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ProtocolError(
                    ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                    f"{field_name} is required",
                )
        if self.causation_id is not None and not self.causation_id.strip():
            raise ProtocolError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                "causation_id cannot be blank",
            )
        _validate_utc(self.created_at_utc, "created_at_utc")
        if self.deadline_at is not None:
            _validate_utc(self.deadline_at, "deadline_at")
        if not isinstance(self.payload, Mapping):
            raise ProtocolError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                "payload must be an object",
            )
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def __repr__(self) -> str:
        return (
            "Envelope("
            f"protocol_version={self.protocol_version!r}, "
            f"message_id={self.message_id!r}, "
            f"correlation_id={self.correlation_id!r}, "
            f"source={self.source!r}, target={self.target!r}, "
            f"message_type={self.message_type!r}, payload=<redacted>)"
        )
