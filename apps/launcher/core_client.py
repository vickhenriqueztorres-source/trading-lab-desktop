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
    CoreDrainRequest,
    CoreDrainResponse,
    CoreLifecycleStatusResponse,
    CoreRestartComponentRequest,
    CoreRestartComponentResponse,
    EndpointRole,
    Envelope,
    LifecycleHandshakeRequest,
    LifecycleHandshakeResponse,
    LifecycleHandshakeStatus,
    MessageType,
    ProtocolError,
    ProtocolErrorCode,
    require_empty_payload,
)
from packages.protocol.transport import FramedSocket
from packages.security import SecretValue


class CoreLifecycleIpcError(RuntimeError):
    reason_code = "LIFECYCLE_IPC_FAILED"

    def __init__(self, reason_code: str | None = None) -> None:
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


class CoreLifecycleIpcUnavailable(CoreLifecycleIpcError):
    reason_code = ProtocolErrorCode.LIFECYCLE_IPC_UNAVAILABLE.value


class CoreLifecycleClient:
    """Authenticated, serialized Launcher-to-Core lifecycle control plane."""

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
        request_timeout: float = 3.0,
    ) -> CoreLifecycleClient:
        if not 0 < port <= 65535 or min(connect_timeout, request_timeout) <= 0:
            raise ValueError("lifecycle endpoint/timeouts are invalid")
        try:
            connection = socket.create_connection(("127.0.0.1", port), timeout=connect_timeout)
        except OSError as exc:
            raise CoreLifecycleIpcUnavailable() from exc
        transport = FramedSocket(connection)
        transport.set_timeout(request_timeout)
        nonce = secrets.token_hex(32)
        request = cls._envelope(
            MessageType.LIFECYCLE_HANDSHAKE_REQUEST,
            LifecycleHandshakeRequest(session_token, nonce, "1.0.0").to_payload(),
            request_timeout,
        )
        try:
            transport.send(request)
            response = transport.receive()
            cls._validate_response(request, response, MessageType.LIFECYCLE_HANDSHAKE_RESPONSE)
            handshake = LifecycleHandshakeResponse.from_payload(response.payload)
            if handshake.status is not LifecycleHandshakeStatus.OK:
                raise CoreLifecycleIpcUnavailable()
            assert handshake.server_nonce is not None
            assert handshake.server_proof is not None
            expected = hmac.new(
                session_token.reveal_bytes(),
                f"{nonce}:{handshake.server_nonce}".encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected, handshake.server_proof):
                raise CoreLifecycleIpcUnavailable()
        except (OSError, ProtocolError, CoreLifecycleIpcError) as exc:
            transport.close()
            if isinstance(exc, CoreLifecycleIpcUnavailable):
                raise
            raise CoreLifecycleIpcUnavailable() from exc
        return cls(transport, request_timeout=request_timeout)

    @property
    def is_ready(self) -> bool:
        return self._ready

    def status(self) -> CoreLifecycleStatusResponse:
        response = self._round_trip(
            MessageType.CORE_LIFECYCLE_STATUS_REQUEST,
            MessageType.CORE_LIFECYCLE_STATUS_RESPONSE,
            {},
        )
        return CoreLifecycleStatusResponse.from_payload(response.payload)

    def safe_stop(self) -> bool:
        return self._empty_ack(MessageType.CORE_SAFE_STOP_REQUEST, MessageType.CORE_SAFE_STOP_ACK)

    def drain(self, timeout: float) -> CoreDrainResponse:
        if not 0 < timeout <= 10:
            raise ValueError("drain timeout is outside bounds")
        request = CoreDrainRequest(max(1, int(timeout * 1000)))
        response = self._round_trip(
            MessageType.CORE_DRAIN_REQUEST,
            MessageType.CORE_DRAIN_RESPONSE,
            request.to_payload(),
            timeout=timeout + 0.5,
        )
        return CoreDrainResponse.from_payload(response.payload)

    def shutdown_workers(self, timeout: float) -> bool:
        return self._empty_ack(
            MessageType.CORE_WORKERS_SHUTDOWN_REQUEST,
            MessageType.CORE_WORKERS_SHUTDOWN_ACK,
            timeout=timeout,
        )

    def shutdown_auth(self, timeout: float) -> bool:
        return self._empty_ack(
            MessageType.CORE_AUTH_SHUTDOWN_REQUEST,
            MessageType.CORE_AUTH_SHUTDOWN_ACK,
            timeout=timeout,
        )

    def restart_component(self, role: str) -> CoreRestartComponentResponse:
        command = CoreRestartComponentRequest(role)
        response = self._round_trip(
            MessageType.CORE_RESTART_COMPONENT_REQUEST,
            MessageType.CORE_RESTART_COMPONENT_RESPONSE,
            command.to_payload(),
        )
        return CoreRestartComponentResponse.from_payload(response.payload)

    def shutdown_core(self, timeout: float) -> bool:
        try:
            return self._empty_ack(
                MessageType.CORE_PROCESS_SHUTDOWN_REQUEST,
                MessageType.CORE_PROCESS_SHUTDOWN_ACK,
                timeout=timeout,
            )
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._ready:
                self._ready = False
                self._transport.close()

    def _empty_ack(
        self,
        request_type: MessageType,
        response_type: MessageType,
        *,
        timeout: float | None = None,
    ) -> bool:
        response = self._round_trip(request_type, response_type, {}, timeout=timeout)
        require_empty_payload(response.payload)
        return True

    def _round_trip(
        self,
        request_type: MessageType,
        response_type: MessageType,
        payload: dict[str, object],
        *,
        timeout: float | None = None,
    ) -> Envelope:
        request_timeout = self._request_timeout if timeout is None else timeout
        if request_timeout <= 0:
            raise ValueError("lifecycle request timeout must be positive")
        request = self._envelope(request_type, payload, request_timeout)
        with self._lock:
            if not self._ready:
                raise CoreLifecycleIpcUnavailable()
            self._transport.set_timeout(request_timeout)
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
                        raise CoreLifecycleIpcUnavailable()
                    raise CoreLifecycleIpcError(reason)
                self._validate_response(request, response, response_type)
                return response
            except (OSError, ProtocolError) as exc:
                self.close()
                raise CoreLifecycleIpcUnavailable() from exc

    @staticmethod
    def _envelope(
        message_type: MessageType,
        payload: dict[str, object],
        timeout: float,
    ) -> Envelope:
        return Envelope(
            protocol_version=PROTOCOL_VERSION,
            message_id=str(uuid4()),
            correlation_id=str(uuid4()),
            causation_id=None,
            source=EndpointRole.LAUNCHER,
            target=EndpointRole.CORE,
            message_type=message_type,
            created_at_utc=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(seconds=timeout),
            payload=payload,
        )

    @staticmethod
    def _validate_response(
        request: Envelope,
        response: Envelope,
        expected_type: MessageType,
    ) -> None:
        if (
            response.protocol_version != PROTOCOL_VERSION
            or response.source is not EndpointRole.CORE
            or response.target is not EndpointRole.LAUNCHER
            or response.message_type is not expected_type
            or response.correlation_id != request.correlation_id
            or response.causation_id != request.message_id
        ):
            raise ProtocolError(
                ProtocolErrorCode.LIFECYCLE_IPC_INVALID_MESSAGE,
                "lifecycle response does not match request",
            )
