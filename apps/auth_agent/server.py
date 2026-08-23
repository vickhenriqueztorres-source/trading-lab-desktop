from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import socket
import threading
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from apps.auth_agent.agent import AuthAgent
from apps.auth_agent.fake_service import (
    FakeIdentityService,
    FakeIdentityServiceError,
    FakeIdentityServiceErrorCode,
)
from apps.auth_agent.vault_factory import create_user_scoped_vault
from packages.identity import OtpCode
from packages.licensing import AuthorizationReason, LeaseVerifier
from packages.protocol import (
    PROTOCOL_VERSION,
    AuthCheckAuthorizationRequest,
    AuthCheckAuthorizationResponse,
    AuthHandshakeRequest,
    AuthHandshakeResponse,
    AuthHandshakeStatus,
    AuthLoginStatus,
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
from packages.security import SecretValue, UserScopedVaultProtocol

_AGENT_VERSION = "1.0.0"
_FAKE_KEY_REGISTRY = "identity.fake_lease_verification_keys"
_DEVICE_ID_KEY = "identity.device_id"
_MAX_VERIFICATION_KEYS = 16
_MAX_CACHED_RESPONSES = 128
_MAX_HANDSHAKE_FAILURES = 8


class AuthAgentServerError(RuntimeError):
    reason_code = "AUTH_AGENT_SERVER_FAILED"

    def __init__(self) -> None:
        super().__init__(self.reason_code)


def _response(
    request: Envelope,
    message_type: MessageType,
    payload: dict[str, object],
) -> Envelope:
    return Envelope(
        protocol_version=PROTOCOL_VERSION,
        message_id=str(uuid4()),
        correlation_id=request.correlation_id,
        causation_id=request.message_id,
        source=EndpointRole.AUTH_AGENT,
        target=EndpointRole.CORE,
        message_type=message_type,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload=payload,
    )


def _error_response(request: Envelope, reason_code: str) -> Envelope:
    return _response(
        request,
        MessageType.ERROR,
        {"reason_code": reason_code, "request_message_id": request.message_id},
    )


def _request_fingerprint(request: Envelope) -> bytes:
    document = {
        "correlation_id": request.correlation_id,
        "message_type": request.message_type.value,
        "payload": dict(request.payload),
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def _decode_key_registry(value: SecretValue | None) -> dict[str, bytes]:
    if value is None:
        return {}
    try:
        document = json.loads(value.reveal_text())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthAgentServerError() from exc
    if not isinstance(document, dict) or len(document) > _MAX_VERIFICATION_KEYS:
        raise AuthAgentServerError()
    result: dict[str, bytes] = {}
    for key_id, encoded in document.items():
        if (
            not isinstance(key_id, str)
            or not key_id.strip()
            or len(key_id) > 128
            or not isinstance(encoded, str)
        ):
            raise AuthAgentServerError()
        try:
            public_key = base64.b64decode(encoded, altchars=b"-_", validate=True)
        except ValueError as exc:
            raise AuthAgentServerError() from exc
        if len(public_key) != 32:
            raise AuthAgentServerError()
        result[key_id] = public_key
    return result


def _encode_key_registry(keys: dict[str, bytes]) -> SecretValue:
    document = {
        key_id: base64.urlsafe_b64encode(public_key).decode("ascii")
        for key_id, public_key in sorted(keys.items())
    }
    return SecretValue(json.dumps(document, sort_keys=True, separators=(",", ":")).encode())


class AuthAgentServer:
    """Isolated Auth Agent server; stored authentication material never returns to the Core."""

    def __init__(
        self,
        session_token: SecretValue,
        profile_dir: Path,
        *,
        force_simulation: bool = False,
        test_otp: OtpCode | None = None,
        lease_ttl: timedelta = timedelta(days=7),
        request_timeout: float = 2.0,
    ) -> None:
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        token_text = session_token.reveal_text()
        if len(token_text) != 64:
            raise ValueError("session token has invalid length")
        try:
            bytes.fromhex(token_text)
        except ValueError as exc:
            raise ValueError("session token has invalid encoding") from exc
        self._session_token = session_token
        self._request_timeout = request_timeout
        self._vault: UserScopedVaultProtocol = create_user_scoped_vault(
            Path(profile_dir) / "vault",
            force_simulation=force_simulation,
        )
        service = FakeIdentityService(
            lease_ttl=lease_ttl,
            signing_key_id=f"fake-{uuid4()}",
            otp_factory=None if test_otp is None else lambda: test_otp,
        )
        verification_keys = _decode_key_registry(self._vault.get_secret(_FAKE_KEY_REGISTRY))
        for key_id, public_key in service.lease_verification_keys.items():
            if key_id not in verification_keys and len(verification_keys) >= _MAX_VERIFICATION_KEYS:
                raise AuthAgentServerError()
            verification_keys[key_id] = public_key
        self._vault.set_secret(_FAKE_KEY_REGISTRY, _encode_key_registry(verification_keys))
        self._agent = AuthAgent(service, self._vault, LeaseVerifier(verification_keys))
        self._startup_decision = self._agent.restore()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        self._listener.settimeout(0.25)
        self._stop = threading.Event()
        self._cache: OrderedDict[str, tuple[bytes, Envelope]] = OrderedDict()
        self._pending_challenge_id: str | None = None
        self._handshake_failures = 0

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
                except ProtocolError:
                    return
                try:
                    self._validate_authenticated_request(request)
                    response = self._dispatch_cached(request)
                except ProtocolError as exc:
                    response = _error_response(request, exc.code.value)
                except (FakeIdentityServiceError, ValueError, RuntimeError):
                    response = _error_response(
                        request,
                        ProtocolErrorCode.AUTH_IPC_INVALID_MESSAGE.value,
                    )
                transport.send(response)
                if request.message_type is MessageType.AUTH_SHUTDOWN_REQUEST:
                    self.stop()
                    return
        finally:
            transport.close()

    def _authenticate(self, transport: FramedSocket) -> bool:
        try:
            request = transport.receive()
            self._validate_handshake_envelope(request)
            handshake = AuthHandshakeRequest.from_payload(request.payload)
        except ProtocolError:
            self._handshake_failures += 1
            if self._handshake_failures >= _MAX_HANDSHAKE_FAILURES:
                self.stop()
            return False
        valid = hmac.compare_digest(
            handshake.session_token.reveal_bytes(),
            self._session_token.reveal_bytes(),
        )
        if not valid:
            denied = AuthHandshakeResponse(
                AuthHandshakeStatus.DENIED,
                _AGENT_VERSION,
                None,
                None,
            )
            transport.send(
                _response(request, MessageType.AUTH_HANDSHAKE_RESPONSE, denied.to_payload())
            )
            self._handshake_failures += 1
            if self._handshake_failures >= _MAX_HANDSHAKE_FAILURES:
                self.stop()
            return False
        server_nonce = secrets.token_hex(32)
        proof = hmac.new(
            self._session_token.reveal_bytes(),
            f"{handshake.client_nonce}:{server_nonce}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        accepted = AuthHandshakeResponse(
            AuthHandshakeStatus.OK,
            _AGENT_VERSION,
            server_nonce,
            proof,
        )
        transport.send(
            _response(request, MessageType.AUTH_HANDSHAKE_RESPONSE, accepted.to_payload())
        )
        return True

    @staticmethod
    def _validate_handshake_envelope(request: Envelope) -> None:
        if (
            request.protocol_version != PROTOCOL_VERSION
            or request.source is not EndpointRole.CORE
            or request.target is not EndpointRole.AUTH_AGENT
            or request.message_type is not MessageType.AUTH_HANDSHAKE_REQUEST
            or request.deadline_at is None
            or request.deadline_at <= datetime.now(UTC)
        ):
            raise ProtocolError(
                ProtocolErrorCode.AUTH_IPC_AUTHENTICATION_FAILED,
                "auth IPC handshake rejected",
            )

    @staticmethod
    def _validate_authenticated_request(request: Envelope) -> None:
        if (
            request.protocol_version != PROTOCOL_VERSION
            or request.source is not EndpointRole.CORE
            or request.target is not EndpointRole.AUTH_AGENT
            or request.message_type is MessageType.AUTH_HANDSHAKE_REQUEST
        ):
            raise ProtocolError(
                ProtocolErrorCode.AUTH_IPC_INVALID_MESSAGE,
                "auth IPC envelope rejected",
            )
        if request.deadline_at is not None and request.deadline_at <= datetime.now(UTC):
            raise ProtocolError(
                ProtocolErrorCode.AUTH_IPC_REQUEST_TIMEOUT,
                "auth IPC request expired",
            )

    def _dispatch_cached(self, request: Envelope) -> Envelope:
        fingerprint = _request_fingerprint(request)
        cached = self._cache.get(request.message_id)
        if cached is not None:
            previous_fingerprint, response = cached
            if not hmac.compare_digest(previous_fingerprint, fingerprint):
                raise ProtocolError(
                    ProtocolErrorCode.AUTH_IPC_DUPLICATE_CONFLICT,
                    "auth IPC message replay conflict",
                )
            self._cache.move_to_end(request.message_id)
            return response
        response = self._dispatch(request)
        self._cache[request.message_id] = (fingerprint, response)
        self._cache.move_to_end(request.message_id)
        while len(self._cache) > _MAX_CACHED_RESPONSES:
            self._cache.popitem(last=False)
        return response

    def _dispatch(self, request: Envelope) -> Envelope:
        if request.message_type is MessageType.AUTH_START_LOGIN_REQUEST:
            login_command = AuthStartLoginRequest.from_payload(request.payload)
            challenge = self._agent.start_login(login_command.email)
            self._pending_challenge_id = challenge.challenge_id
            login_result = AuthStartLoginResponse(
                AuthLoginStatus.CHALLENGE_CREATED,
                challenge.challenge_id,
            )
            return _response(
                request,
                MessageType.AUTH_START_LOGIN_RESPONSE,
                login_result.to_payload(),
            )
        if request.message_type is MessageType.AUTH_SUBMIT_OTP_REQUEST:
            otp_command = AuthSubmitOtpRequest.from_payload(request.payload)
            if otp_command.challenge_id != self._pending_challenge_id:
                raise ProtocolError(
                    ProtocolErrorCode.AUTH_IPC_INVALID_MESSAGE,
                    "auth challenge does not match pending login",
                )
            try:
                decision = self._agent.complete_login(OtpCode(otp_command.otp_code.reveal_text()))
            except FakeIdentityServiceError as exc:
                status = (
                    AuthLoginStatus.INVALID_CODE
                    if exc.code is FakeIdentityServiceErrorCode.OTP_INVALID
                    else AuthLoginStatus.BLOCKED
                )
                otp_result = AuthSubmitOtpResponse(status, None)
            else:
                self._pending_challenge_id = None
                otp_result = AuthSubmitOtpResponse(
                    AuthLoginStatus.AUTHORIZED
                    if decision.new_entries_allowed
                    else AuthLoginStatus.BLOCKED,
                    self._agent.user_id_preview,
                )
            return _response(
                request,
                MessageType.AUTH_SUBMIT_OTP_RESPONSE,
                otp_result.to_payload(),
            )
        if request.message_type is MessageType.AUTH_CHECK_AUTHORIZATION_REQUEST:
            authorization_command = AuthCheckAuthorizationRequest.from_payload(request.payload)
            if (
                self._agent.current_lease is None
                and self._startup_decision.reason is AuthorizationReason.LEASE_INVALID
            ):
                decision = self._startup_decision
            else:
                decision = self._agent.authorization(
                    authorization_command.broker,
                    authorization_command.strategy_pack,
                    real_mode=authorization_command.mode.value == "REAL",
                )
            authorization_result = AuthCheckAuthorizationResponse(
                decision.new_entries_allowed,
                decision.reason.value,
                decision.expires_at,
            )
            return _response(
                request,
                MessageType.AUTH_CHECK_AUTHORIZATION_RESPONSE,
                authorization_result.to_payload(),
            )
        if request.message_type is MessageType.AUTH_RENEW_REQUEST:
            require_empty_payload(request.payload)
            decision = self._agent.renew_silently()
            renewal_result = AuthCheckAuthorizationResponse(
                decision.new_entries_allowed,
                decision.reason.value,
                decision.expires_at,
            )
            return _response(
                request,
                MessageType.AUTH_RENEW_RESPONSE,
                renewal_result.to_payload(),
            )
        if request.message_type is MessageType.AUTH_STATUS_REQUEST:
            require_empty_payload(request.payload)
            stored_device = self._vault.get_secret(_DEVICE_ID_KEY)
            status_result = AuthStatusResponse(
                auth_state=self._agent.state.value,
                user_id_preview=self._agent.user_id_preview,
                device_id=None if stored_device is None else stored_device.reveal_text(),
                lease_active=self._agent.authorization(
                    "DERIV", "strategy-test"
                ).new_entries_allowed,
            )
            return _response(
                request,
                MessageType.AUTH_STATUS_RESPONSE,
                status_result.to_payload(),
            )
        if request.message_type is MessageType.AUTH_SHUTDOWN_REQUEST:
            require_empty_payload(request.payload)
            return _response(request, MessageType.AUTH_SHUTDOWN_ACK, {})
        raise ProtocolError(
            ProtocolErrorCode.AUTH_IPC_INVALID_MESSAGE,
            "auth IPC message type is not accepted",
        )
