from __future__ import annotations

import hashlib
import hmac
import secrets
import socket
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from uuid import uuid4

from apps.core.lifecycle_service import CoreLifecycleService
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
from packages.protocol.codec import encode_envelope
from packages.protocol.transport import FramedSocket
from packages.security import SecretValue

_CORE_VERSION = "1.0.0"
_MAX_CACHE = 128


def _response(request: Envelope, message_type: MessageType, payload: dict[str, object]) -> Envelope:
    return Envelope(
        protocol_version=PROTOCOL_VERSION,
        message_id=str(uuid4()),
        correlation_id=request.correlation_id,
        causation_id=request.message_id,
        source=EndpointRole.CORE,
        target=EndpointRole.LAUNCHER,
        message_type=message_type,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload=payload,
    )


def _error(request: Envelope, code: str) -> Envelope:
    return _response(
        request,
        MessageType.ERROR,
        {"reason_code": code, "request_message_id": request.message_id},
    )


class CoreLifecycleServer:
    def __init__(
        self,
        service: CoreLifecycleService,
        session_token: SecretValue,
        *,
        request_timeout: float = 2.0,
    ) -> None:
        if request_timeout <= 0:
            raise ValueError("lifecycle request timeout must be positive")
        token = session_token.reveal_text()
        if len(token) != 64:
            raise ValueError("lifecycle session token is invalid")
        bytes.fromhex(token)
        self._service = service
        self._token = session_token
        self._request_timeout = request_timeout
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(2)
        self._listener.settimeout(0.25)
        self._stop = threading.Event()
        self._cache: OrderedDict[str, tuple[bytes, Envelope]] = OrderedDict()

    @property
    def port(self) -> int:
        return int(self._listener.getsockname()[1])

    def serve_forever(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    connection, address = self._listener.accept()
                except TimeoutError:
                    continue
                if address[0] != "127.0.0.1":
                    connection.close()
                    continue
                self._serve_connection(FramedSocket(connection))
        finally:
            self._listener.close()

    def stop(self) -> None:
        self._stop.set()

    def _serve_connection(self, transport: FramedSocket) -> None:
        try:
            transport.set_timeout(self._request_timeout)
            if not self._authenticate(transport):
                return
            while not self._stop.is_set():
                try:
                    request = transport.receive()
                    self._validate_request(request)
                    response = self._dispatch_cached(request)
                except ProtocolError:
                    return
                except (OSError, RuntimeError, ValueError):
                    response = _error(
                        request,
                        ProtocolErrorCode.LIFECYCLE_IPC_INVALID_MESSAGE.value,
                    )
                transport.send(response)
                if request.message_type is MessageType.CORE_PROCESS_SHUTDOWN_REQUEST:
                    self.stop()
                    return
        finally:
            transport.close()

    def _authenticate(self, transport: FramedSocket) -> bool:
        try:
            request = transport.receive()
            if (
                request.protocol_version != PROTOCOL_VERSION
                or request.source is not EndpointRole.LAUNCHER
                or request.target is not EndpointRole.CORE
                or request.message_type is not MessageType.LIFECYCLE_HANDSHAKE_REQUEST
                or request.deadline_at is None
                or request.deadline_at <= datetime.now(UTC)
            ):
                raise ProtocolError(
                    ProtocolErrorCode.LIFECYCLE_IPC_AUTHENTICATION_FAILED,
                    "lifecycle handshake rejected",
                )
            handshake = LifecycleHandshakeRequest.from_payload(request.payload)
        except ProtocolError:
            return False
        if not hmac.compare_digest(
            handshake.session_token.reveal_bytes(), self._token.reveal_bytes()
        ):
            denied = LifecycleHandshakeResponse(
                LifecycleHandshakeStatus.DENIED, _CORE_VERSION, None, None
            )
            transport.send(
                _response(request, MessageType.LIFECYCLE_HANDSHAKE_RESPONSE, denied.to_payload())
            )
            return False
        server_nonce = secrets.token_hex(32)
        proof = hmac.new(
            self._token.reveal_bytes(),
            f"{handshake.client_nonce}:{server_nonce}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        accepted = LifecycleHandshakeResponse(
            LifecycleHandshakeStatus.OK, _CORE_VERSION, server_nonce, proof
        )
        transport.send(
            _response(request, MessageType.LIFECYCLE_HANDSHAKE_RESPONSE, accepted.to_payload())
        )
        return True

    @staticmethod
    def _validate_request(request: Envelope) -> None:
        if (
            request.protocol_version != PROTOCOL_VERSION
            or request.source is not EndpointRole.LAUNCHER
            or request.target is not EndpointRole.CORE
            or request.message_type is MessageType.LIFECYCLE_HANDSHAKE_REQUEST
            or request.deadline_at is None
            or request.deadline_at <= datetime.now(UTC)
        ):
            raise ProtocolError(
                ProtocolErrorCode.LIFECYCLE_IPC_INVALID_MESSAGE,
                "lifecycle request envelope rejected",
            )

    def _dispatch_cached(self, request: Envelope) -> Envelope:
        fingerprint = hashlib.sha256(encode_envelope(request)).digest()
        cached = self._cache.get(request.message_id)
        if cached is not None:
            previous, response = cached
            if not hmac.compare_digest(previous, fingerprint):
                raise ProtocolError(
                    ProtocolErrorCode.LIFECYCLE_IPC_DUPLICATE_CONFLICT,
                    "lifecycle message replay conflict",
                )
            return response
        response = self._dispatch(request)
        self._cache[request.message_id] = (fingerprint, response)
        while len(self._cache) > _MAX_CACHE:
            self._cache.popitem(last=False)
        return response

    def _dispatch(self, request: Envelope) -> Envelope:
        if request.message_type is MessageType.CORE_LIFECYCLE_STATUS_REQUEST:
            require_empty_payload(request.payload)
            status = CoreLifecycleStatusResponse(
                self._service.state.value,
                self._service.safe_stop_active,
                self._service.process_statuses(),
                bool(getattr(self._service, "ui_shutdown_requested", False)),
            )
            return _response(
                request, MessageType.CORE_LIFECYCLE_STATUS_RESPONSE, status.to_payload()
            )
        if request.message_type is MessageType.CORE_SAFE_STOP_REQUEST:
            require_empty_payload(request.payload)
            self._service.safe_stop()
            return _response(request, MessageType.CORE_SAFE_STOP_ACK, {})
        if request.message_type is MessageType.CORE_DRAIN_REQUEST:
            drain = CoreDrainRequest.from_payload(request.payload)
            drained, pending = self._service.drain(drain.timeout_milliseconds / 1000.0)
            drain_result = CoreDrainResponse(drained, pending)
            return _response(
                request,
                MessageType.CORE_DRAIN_RESPONSE,
                drain_result.to_payload(),
            )
        if request.message_type is MessageType.CORE_WORKERS_SHUTDOWN_REQUEST:
            require_empty_payload(request.payload)
            self._service.shutdown_workers(self._request_timeout)
            return _response(request, MessageType.CORE_WORKERS_SHUTDOWN_ACK, {})
        if request.message_type is MessageType.CORE_AUTH_SHUTDOWN_REQUEST:
            require_empty_payload(request.payload)
            self._service.shutdown_auth(self._request_timeout)
            return _response(request, MessageType.CORE_AUTH_SHUTDOWN_ACK, {})
        if request.message_type is MessageType.CORE_RESTART_COMPONENT_REQUEST:
            command = CoreRestartComponentRequest.from_payload(request.payload)
            try:
                accepted, reason = self._service.restart_component(command.role)
            except RuntimeError:
                accepted, reason = False, "RESTART_FAILED"
            restart_result = CoreRestartComponentResponse(accepted, reason)
            return _response(
                request,
                MessageType.CORE_RESTART_COMPONENT_RESPONSE,
                restart_result.to_payload(),
            )
        if request.message_type is MessageType.CORE_PROCESS_SHUTDOWN_REQUEST:
            require_empty_payload(request.payload)
            self._service.shutdown_core()
            return _response(request, MessageType.CORE_PROCESS_SHUTDOWN_ACK, {})
        raise ProtocolError(
            ProtocolErrorCode.LIFECYCLE_IPC_INVALID_MESSAGE,
            "lifecycle message type is unsupported",
        )
