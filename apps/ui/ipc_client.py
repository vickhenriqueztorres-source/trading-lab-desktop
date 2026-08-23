from __future__ import annotations

import hashlib
import hmac
import secrets
import socket
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from packages.protocol import (
    PROTOCOL_VERSION,
    EndpointRole,
    Envelope,
    MessageType,
    ProtocolError,
    ProtocolErrorCode,
    UiCommandAck,
    UiGenerateDiagnosticResponse,
    UiHandshakeRequest,
    UiHandshakeResponse,
    UiHandshakeStatus,
    UiProjectionSnapshot,
)
from packages.protocol.transport import FramedSocket
from packages.security import SecretValue


class UiIpcError(RuntimeError):
    reason_code = "UI_IPC_FAILED"

    def __init__(self, reason_code: str | None = None) -> None:
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


class UiIpcUnavailable(UiIpcError):
    reason_code = ProtocolErrorCode.UI_IPC_UNAVAILABLE.value


class UiIpcClient:
    """Authenticated serialized UI client; never persists or talks to a broker."""

    def __init__(self, transport: FramedSocket, *, request_timeout: float) -> None:
        self._transport = transport
        self._request_timeout = request_timeout
        self._lock = threading.RLock()
        self._ready = True

    @classmethod
    def connect(
        cls,
        port: int,
        session_token: SecretValue,
        *,
        connect_timeout: float = 2.0,
        request_timeout: float = 2.0,
    ) -> UiIpcClient:
        if not 0 < port <= 65535 or min(connect_timeout, request_timeout) <= 0:
            raise ValueError("UI endpoint/timeouts are invalid")
        try:
            connection = socket.create_connection(("127.0.0.1", port), timeout=connect_timeout)
        except OSError as exc:
            raise UiIpcUnavailable() from exc
        transport = FramedSocket(connection)
        transport.set_timeout(request_timeout)
        nonce = secrets.token_hex(32)
        request = cls._envelope(
            MessageType.UI_HANDSHAKE_REQUEST,
            UiHandshakeRequest(session_token, "1.0.0", nonce).to_payload(),
            request_timeout,
        )
        try:
            transport.send(request)
            response = transport.receive()
            cls._validate_response(request, response, MessageType.UI_HANDSHAKE_RESPONSE)
            handshake = UiHandshakeResponse.from_payload(response.payload)
            if handshake.status is not UiHandshakeStatus.OK:
                raise UiIpcUnavailable()
            assert handshake.server_nonce is not None
            assert handshake.server_proof is not None
            expected = hmac.new(
                session_token.reveal_bytes(),
                f"{nonce}:{handshake.server_nonce}".encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected, handshake.server_proof):
                raise UiIpcUnavailable()
        except (OSError, ProtocolError, UiIpcError) as exc:
            transport.close()
            if isinstance(exc, UiIpcUnavailable):
                raise
            raise UiIpcUnavailable() from exc
        return cls(transport, request_timeout=request_timeout)

    @property
    def is_ready(self) -> bool:
        return self._ready

    def projection(self) -> UiProjectionSnapshot:
        response = self._round_trip(
            MessageType.UI_PROJECTION_REQUEST, MessageType.UI_PROJECTION_SNAPSHOT
        )
        return UiProjectionSnapshot.from_payload(response.payload)

    def safe_stop(self) -> UiCommandAck:
        return self._command(MessageType.UI_SAFE_STOP_COMMAND, MessageType.UI_SAFE_STOP_ACK)

    def resume(self) -> UiCommandAck:
        return self._command(MessageType.UI_RESUME_COMMAND, MessageType.UI_RESUME_ACK)

    def request_shutdown(self) -> UiCommandAck:
        return self._command(MessageType.UI_SHUTDOWN_REQUEST, MessageType.UI_SHUTDOWN_ACK)

    def generate_diagnostic(self) -> UiGenerateDiagnosticResponse:
        response = self._round_trip(
            MessageType.UI_GENERATE_DIAGNOSTIC_COMMAND,
            MessageType.UI_GENERATE_DIAGNOSTIC_RESPONSE,
        )
        return UiGenerateDiagnosticResponse.from_payload(response.payload)

    def close(self) -> None:
        with self._lock:
            if self._ready:
                self._ready = False
                self._transport.close()

    def _command(self, request: MessageType, response: MessageType) -> UiCommandAck:
        return UiCommandAck.from_payload(self._round_trip(request, response).payload)

    def _round_trip(self, request_type: MessageType, response_type: MessageType) -> Envelope:
        request = self._envelope(request_type, {}, self._request_timeout)
        with self._lock:
            if not self._ready:
                raise UiIpcUnavailable()
            self._transport.set_timeout(self._request_timeout)
            try:
                self._transport.send(request)
                response = self._transport.receive()
                if response.message_type is MessageType.ERROR:
                    self._validate_response(request, response, MessageType.ERROR)
                    reason = response.payload.get("reason_code")
                    request_id = response.payload.get("request_message_id")
                    if (
                        set(response.payload) != {"reason_code", "request_message_id"}
                        or not isinstance(reason, str)
                        or request_id != request.message_id
                    ):
                        raise UiIpcUnavailable()
                    raise UiIpcError(reason)
                self._validate_response(request, response, response_type)
                return response
            except (OSError, ProtocolError) as exc:
                self.close()
                raise UiIpcUnavailable() from exc

    @staticmethod
    def _envelope(kind: MessageType, payload: dict[str, object], timeout: float) -> Envelope:
        return Envelope(
            protocol_version=PROTOCOL_VERSION,
            message_id=str(uuid4()),
            correlation_id=str(uuid4()),
            causation_id=None,
            source=EndpointRole.UI,
            target=EndpointRole.CORE,
            message_type=kind,
            created_at_utc=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(seconds=timeout),
            payload=payload,
        )

    @staticmethod
    def _validate_response(request: Envelope, response: Envelope, expected: MessageType) -> None:
        if (
            response.protocol_version != PROTOCOL_VERSION
            or response.source is not EndpointRole.CORE
            or response.target is not EndpointRole.UI
            or response.message_type is not expected
            or response.correlation_id != request.correlation_id
            or response.causation_id != request.message_id
        ):
            raise ProtocolError(
                ProtocolErrorCode.UI_IPC_INVALID_MESSAGE,
                "UI response does not match request",
            )
