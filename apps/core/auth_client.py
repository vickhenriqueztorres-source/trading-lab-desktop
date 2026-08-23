from __future__ import annotations

import hashlib
import hmac
import secrets
import socket
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from packages.identity import OtpCode
from packages.protocol import (
    PROTOCOL_VERSION,
    AuthCheckAuthorizationRequest,
    AuthCheckAuthorizationResponse,
    AuthHandshakeRequest,
    AuthHandshakeResponse,
    AuthHandshakeStatus,
    AuthMode,
    AuthStartLoginRequest,
    AuthStartLoginResponse,
    AuthStatusResponse,
    AuthSubmitOtpRequest,
    AuthSubmitOtpResponse,
    EndpointRole,
    Envelope,
    MessageType,
    ProtocolError,
    ProtocolErrorCode,
    require_empty_payload,
)
from packages.protocol.transport import FramedSocket
from packages.security import SecretValue


class AuthIpcError(RuntimeError):
    reason_code = "AUTH_IPC_FAILED"

    def __init__(self, reason_code: str | None = None) -> None:
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


class AuthIpcAuthenticationError(AuthIpcError):
    reason_code = ProtocolErrorCode.AUTH_IPC_AUTHENTICATION_FAILED.value


class AuthIpcUnavailable(AuthIpcError):
    reason_code = ProtocolErrorCode.AUTH_IPC_UNAVAILABLE.value


class AuthAgentIpcClient:
    """Synchronous bounded client for the reduced Auth Agent boundary."""

    def __init__(
        self,
        transport: FramedSocket,
        *,
        request_timeout: float,
    ) -> None:
        self._transport = transport
        self._request_timeout = request_timeout
        self._lock = threading.RLock()
        self._ready = True

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        session_token: SecretValue,
        *,
        connect_timeout: float = 2.0,
        request_timeout: float = 2.0,
        client_version: str = "1.0.0",
    ) -> AuthAgentIpcClient:
        if host != "127.0.0.1" or not 0 < port <= 65535:
            raise ValueError("auth IPC endpoint must be IPv4 loopback")
        if min(connect_timeout, request_timeout) <= 0:
            raise ValueError("auth IPC timeouts must be positive")
        try:
            connection = socket.create_connection((host, port), timeout=connect_timeout)
        except OSError as exc:
            raise AuthIpcUnavailable() from exc
        transport = FramedSocket(connection)
        transport.set_timeout(request_timeout)
        client_nonce = secrets.token_hex(32)
        request = Envelope(
            protocol_version=PROTOCOL_VERSION,
            message_id=str(uuid4()),
            correlation_id=str(uuid4()),
            causation_id=None,
            source=EndpointRole.CORE,
            target=EndpointRole.AUTH_AGENT,
            message_type=MessageType.AUTH_HANDSHAKE_REQUEST,
            created_at_utc=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(seconds=request_timeout),
            payload=AuthHandshakeRequest(
                session_token,
                client_version,
                client_nonce,
            ).to_payload(),
        )
        try:
            transport.send(request)
            response = transport.receive()
            cls._validate_response(
                request,
                response,
                MessageType.AUTH_HANDSHAKE_RESPONSE,
            )
            handshake = AuthHandshakeResponse.from_payload(response.payload)
            if handshake.status is not AuthHandshakeStatus.OK:
                raise AuthIpcAuthenticationError()
            assert handshake.server_nonce is not None
            assert handshake.server_proof is not None
            expected_proof = hmac.new(
                session_token.reveal_bytes(),
                f"{client_nonce}:{handshake.server_nonce}".encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected_proof, handshake.server_proof):
                raise AuthIpcAuthenticationError()
        except (ProtocolError, AuthIpcError, OSError) as exc:
            transport.close()
            if isinstance(exc, AuthIpcAuthenticationError):
                raise
            raise AuthIpcAuthenticationError() from exc
        return cls(transport, request_timeout=request_timeout)

    @property
    def is_ready(self) -> bool:
        return self._ready

    def start_login(self, email: str) -> AuthStartLoginResponse:
        response = self._round_trip(
            MessageType.AUTH_START_LOGIN_REQUEST,
            MessageType.AUTH_START_LOGIN_RESPONSE,
            AuthStartLoginRequest(email).to_payload(),
        )
        return AuthStartLoginResponse.from_payload(response.payload)

    def submit_otp(self, challenge_id: str, code: OtpCode) -> AuthSubmitOtpResponse:
        response = self._round_trip(
            MessageType.AUTH_SUBMIT_OTP_REQUEST,
            MessageType.AUTH_SUBMIT_OTP_RESPONSE,
            AuthSubmitOtpRequest(
                challenge_id,
                SecretValue.from_text(code.value),
            ).to_payload(),
        )
        return AuthSubmitOtpResponse.from_payload(response.payload)

    def check_authorization(
        self,
        broker: str,
        strategy_pack: str,
        *,
        mode: AuthMode = AuthMode.PRACTICE,
    ) -> AuthCheckAuthorizationResponse:
        response = self._round_trip(
            MessageType.AUTH_CHECK_AUTHORIZATION_REQUEST,
            MessageType.AUTH_CHECK_AUTHORIZATION_RESPONSE,
            AuthCheckAuthorizationRequest(broker, strategy_pack, mode).to_payload(),
        )
        return AuthCheckAuthorizationResponse.from_payload(response.payload)

    def status(self) -> AuthStatusResponse:
        response = self._round_trip(
            MessageType.AUTH_STATUS_REQUEST,
            MessageType.AUTH_STATUS_RESPONSE,
            {},
        )
        return AuthStatusResponse.from_payload(response.payload)

    def renew(self) -> AuthCheckAuthorizationResponse:
        response = self._round_trip(
            MessageType.AUTH_RENEW_REQUEST,
            MessageType.AUTH_RENEW_RESPONSE,
            {},
        )
        return AuthCheckAuthorizationResponse.from_payload(response.payload)

    def shutdown(self, timeout: float | None = None) -> bool:
        if not self._ready:
            return False
        try:
            response = self._round_trip(
                MessageType.AUTH_SHUTDOWN_REQUEST,
                MessageType.AUTH_SHUTDOWN_ACK,
                {},
                timeout=timeout,
            )
            require_empty_payload(response.payload)
            return True
        except AuthIpcError:
            return False
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._ready:
                self._ready = False
                self._transport.close()

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
            raise ValueError("auth IPC timeout must be positive")
        request = Envelope(
            protocol_version=PROTOCOL_VERSION,
            message_id=str(uuid4()),
            correlation_id=str(uuid4()),
            causation_id=None,
            source=EndpointRole.CORE,
            target=EndpointRole.AUTH_AGENT,
            message_type=request_type,
            created_at_utc=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(seconds=request_timeout),
            payload=payload,
        )
        with self._lock:
            if not self._ready:
                raise AuthIpcUnavailable()
            self._transport.set_timeout(request_timeout)
            try:
                self._transport.send(request)
                response = self._transport.receive()
                if response.message_type is MessageType.ERROR:
                    self._validate_response(request, response, MessageType.ERROR)
                    if set(response.payload) != {"reason_code", "request_message_id"}:
                        raise AuthIpcUnavailable()
                    reason = response.payload.get("reason_code")
                    request_message_id = response.payload.get("request_message_id")
                    if (
                        not isinstance(reason, str)
                        or not reason
                        or len(reason) > 64
                        or request_message_id != request.message_id
                    ):
                        raise AuthIpcUnavailable()
                    raise AuthIpcError(reason)
                self._validate_response(request, response, response_type)
                return response
            except AuthIpcError:
                raise
            except (OSError, ProtocolError) as exc:
                self._ready = False
                self._transport.close()
                raise AuthIpcUnavailable() from exc

    @staticmethod
    def _validate_response(
        request: Envelope,
        response: Envelope,
        expected_type: MessageType,
    ) -> None:
        if (
            response.protocol_version != PROTOCOL_VERSION
            or response.source is not EndpointRole.AUTH_AGENT
            or response.target is not EndpointRole.CORE
            or response.message_type is not expected_type
            or response.correlation_id != request.correlation_id
            or response.causation_id != request.message_id
        ):
            raise ProtocolError(
                ProtocolErrorCode.AUTH_IPC_INVALID_MESSAGE,
                "auth IPC response failed correlation validation",
            )
