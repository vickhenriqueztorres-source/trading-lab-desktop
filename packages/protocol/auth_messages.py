from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from packages.domain.models import require_aware_utc
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.security import SecretValue

_MAX_IDENTIFIER_CHARS = 128
_MAX_EMAIL_CHARS = 254
_SESSION_TOKEN_CHARS = 64
_NONCE_CHARS = 64


class AuthHandshakeStatus(StrEnum):
    OK = "OK"
    DENIED = "DENIED"


class AuthLoginStatus(StrEnum):
    CHALLENGE_CREATED = "CHALLENGE_CREATED"
    AUTHORIZED = "AUTHORIZED"
    INVALID_CODE = "INVALID_CODE"
    BLOCKED = "BLOCKED"


class AuthMode(StrEnum):
    PRACTICE = "PRACTICE"
    DEMO = "DEMO"
    REAL = "REAL"


def _invalid() -> ProtocolError:
    return ProtocolError(
        ProtocolErrorCode.AUTH_IPC_INVALID_MESSAGE,
        "auth IPC payload is invalid",
    )


def _exact(payload: Mapping[str, object], fields: set[str]) -> None:
    if set(payload) != fields:
        raise _invalid()


def _string(
    payload: Mapping[str, object],
    field: str,
    *,
    maximum: int = _MAX_IDENTIFIER_CHARS,
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise _invalid()
    return value.strip()


def _optional_string(
    payload: Mapping[str, object],
    field: str,
    *,
    maximum: int = _MAX_IDENTIFIER_CHARS,
) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise _invalid()
    return value.strip()


@dataclass(frozen=True, slots=True, repr=False)
class AuthHandshakeRequest:
    session_token: SecretValue
    client_version: str
    client_nonce: str

    def to_payload(self) -> dict[str, object]:
        return {
            "auth_token": self.session_token.reveal_text(),
            "client_nonce": self.client_nonce,
            "client_version": self.client_version,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> AuthHandshakeRequest:
        _exact(payload, {"auth_token", "client_nonce", "client_version"})
        token = _string(payload, "auth_token", maximum=_SESSION_TOKEN_CHARS)
        nonce = _string(payload, "client_nonce", maximum=_NONCE_CHARS)
        version = _string(payload, "client_version", maximum=32)
        if len(token) != _SESSION_TOKEN_CHARS or len(nonce) != _NONCE_CHARS:
            raise _invalid()
        try:
            bytes.fromhex(token)
            bytes.fromhex(nonce)
        except ValueError as exc:
            raise _invalid() from exc
        return cls(SecretValue.from_text(token), version, nonce)

    def __repr__(self) -> str:
        return "AuthHandshakeRequest(<redacted>)"


@dataclass(frozen=True, slots=True)
class AuthHandshakeResponse:
    status: AuthHandshakeStatus
    agent_version: str
    server_nonce: str | None
    server_proof: str | None

    def to_payload(self) -> dict[str, object]:
        return {
            "agent_version": self.agent_version,
            "server_nonce": self.server_nonce,
            "server_proof": self.server_proof,
            "status": self.status.value,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> AuthHandshakeResponse:
        _exact(payload, {"agent_version", "server_nonce", "server_proof", "status"})
        try:
            status = AuthHandshakeStatus(_string(payload, "status", maximum=16))
        except ValueError as exc:
            raise _invalid() from exc
        agent_version = _string(payload, "agent_version", maximum=32)
        server_nonce = _optional_string(payload, "server_nonce", maximum=_NONCE_CHARS)
        server_proof = _optional_string(payload, "server_proof", maximum=64)
        if status is AuthHandshakeStatus.OK:
            if server_nonce is None or server_proof is None:
                raise _invalid()
            try:
                if len(server_nonce) != _NONCE_CHARS or len(server_proof) != 64:
                    raise ValueError
                bytes.fromhex(server_nonce)
                bytes.fromhex(server_proof)
            except ValueError as exc:
                raise _invalid() from exc
        elif server_nonce is not None or server_proof is not None:
            raise _invalid()
        return cls(status, agent_version, server_nonce, server_proof)


@dataclass(frozen=True, slots=True, repr=False)
class AuthStartLoginRequest:
    email: str

    def to_payload(self) -> dict[str, object]:
        return {"email": self.email}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> AuthStartLoginRequest:
        _exact(payload, {"email"})
        email = _string(payload, "email", maximum=_MAX_EMAIL_CHARS)
        if "@" not in email:
            raise _invalid()
        return cls(email)

    def __repr__(self) -> str:
        return "AuthStartLoginRequest(<redacted>)"


@dataclass(frozen=True, slots=True)
class AuthStartLoginResponse:
    status: AuthLoginStatus
    challenge_id: str

    def to_payload(self) -> dict[str, object]:
        return {"challenge_id": self.challenge_id, "status": self.status.value}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> AuthStartLoginResponse:
        _exact(payload, {"challenge_id", "status"})
        try:
            status = AuthLoginStatus(_string(payload, "status", maximum=32))
        except ValueError as exc:
            raise _invalid() from exc
        if status is not AuthLoginStatus.CHALLENGE_CREATED:
            raise _invalid()
        return cls(status, _string(payload, "challenge_id"))


@dataclass(frozen=True, slots=True, repr=False)
class AuthSubmitOtpRequest:
    challenge_id: str
    otp_code: SecretValue

    def to_payload(self) -> dict[str, object]:
        return {
            "challenge_id": self.challenge_id,
            "otp_code": self.otp_code.reveal_text(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> AuthSubmitOtpRequest:
        _exact(payload, {"challenge_id", "otp_code"})
        challenge_id = _string(payload, "challenge_id")
        code = _string(payload, "otp_code", maximum=6)
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            raise _invalid()
        return cls(challenge_id, SecretValue.from_text(code))

    def __repr__(self) -> str:
        return "AuthSubmitOtpRequest(<redacted>)"


@dataclass(frozen=True, slots=True)
class AuthSubmitOtpResponse:
    status: AuthLoginStatus
    user_id_preview: str | None

    def to_payload(self) -> dict[str, object]:
        return {"status": self.status.value, "user_id_preview": self.user_id_preview}

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> AuthSubmitOtpResponse:
        _exact(payload, {"status", "user_id_preview"})
        try:
            status = AuthLoginStatus(_string(payload, "status", maximum=32))
        except ValueError as exc:
            raise _invalid() from exc
        if status is AuthLoginStatus.CHALLENGE_CREATED:
            raise _invalid()
        return cls(status, _optional_string(payload, "user_id_preview", maximum=16))


@dataclass(frozen=True, slots=True)
class AuthCheckAuthorizationRequest:
    broker: str
    strategy_pack: str
    mode: AuthMode

    def to_payload(self) -> dict[str, object]:
        return {
            "broker": self.broker,
            "mode": self.mode.value,
            "strategy_pack": self.strategy_pack,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> AuthCheckAuthorizationRequest:
        _exact(payload, {"broker", "mode", "strategy_pack"})
        try:
            mode = AuthMode(_string(payload, "mode", maximum=16))
        except ValueError as exc:
            raise _invalid() from exc
        return cls(
            broker=_string(payload, "broker", maximum=32),
            strategy_pack=_string(payload, "strategy_pack"),
            mode=mode,
        )


@dataclass(frozen=True, slots=True)
class AuthCheckAuthorizationResponse:
    allowed: bool
    reason_code: str
    lease_expires_at_utc: datetime | None

    def to_payload(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "lease_expires_at_utc": (
                None if self.lease_expires_at_utc is None else self.lease_expires_at_utc.isoformat()
            ),
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> AuthCheckAuthorizationResponse:
        _exact(payload, {"allowed", "lease_expires_at_utc", "reason_code"})
        allowed = payload.get("allowed")
        if not isinstance(allowed, bool):
            raise _invalid()
        reason = _string(payload, "reason_code", maximum=64)
        raw_expiry = payload.get("lease_expires_at_utc")
        if raw_expiry is None:
            expiry = None
        elif isinstance(raw_expiry, str):
            try:
                expiry = datetime.fromisoformat(raw_expiry)
                require_aware_utc(expiry, "lease_expires_at_utc")
            except ValueError as exc:
                raise _invalid() from exc
        else:
            raise _invalid()
        return cls(allowed, reason, expiry)


@dataclass(frozen=True, slots=True)
class AuthStatusResponse:
    auth_state: str
    user_id_preview: str | None
    device_id: str | None
    lease_active: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "auth_state": self.auth_state,
            "device_id": self.device_id,
            "lease_active": self.lease_active,
            "user_id_preview": self.user_id_preview,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> AuthStatusResponse:
        _exact(payload, {"auth_state", "device_id", "lease_active", "user_id_preview"})
        active = payload.get("lease_active")
        if not isinstance(active, bool):
            raise _invalid()
        return cls(
            auth_state=_string(payload, "auth_state", maximum=32),
            user_id_preview=_optional_string(payload, "user_id_preview", maximum=16),
            device_id=_optional_string(payload, "device_id"),
            lease_active=active,
        )


def require_empty_payload(payload: Mapping[str, object]) -> None:
    _exact(payload, set())
