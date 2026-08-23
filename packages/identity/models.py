from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime

from packages.domain.models import require_aware_utc
from packages.security import SecretValue


def normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    local, separator, domain = normalized.partition("@")
    if not separator or not local or not domain or "." not in domain or len(normalized) > 254:
        raise ValueError("email is invalid")
    return normalized


@dataclass(frozen=True, slots=True, repr=False)
class OtpCode:
    value: str

    def __post_init__(self) -> None:
        if len(self.value) != 6 or not self.value.isascii() or not self.value.isdigit():
            raise ValueError("OTP must contain exactly six ASCII digits")

    def __repr__(self) -> str:
        return "OtpCode(<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class PkceMaterial:
    verifier: SecretValue
    challenge: str

    @classmethod
    def create(cls) -> PkceMaterial:
        verifier_text = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier_text.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return cls(SecretValue.from_text(verifier_text), challenge)

    @staticmethod
    def challenge_for(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class LoginChallenge:
    challenge_id: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.challenge_id.strip():
            raise ValueError("challenge_id cannot be empty")
        require_aware_utc(self.expires_at, "expires_at")


@dataclass(frozen=True, slots=True)
class SessionTokens:
    user_id: str
    access_token: SecretValue
    refresh_token: SecretValue
    access_expires_at: datetime

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("user_id cannot be empty")
        require_aware_utc(self.access_expires_at, "access_expires_at")

    @classmethod
    def from_external_payload(cls, payload: object) -> SessionTokens:
        if not isinstance(payload, dict):
            raise ValueError("session response must be an object")
        required = {"user_id", "access_token", "refresh_token", "access_expires_at"}
        if set(payload) != required or not all(isinstance(payload[key], str) for key in required):
            raise ValueError("session response schema is invalid")
        try:
            expires_at = datetime.fromisoformat(payload["access_expires_at"])
        except ValueError as exc:
            raise ValueError("access_expires_at is invalid") from exc
        return cls(
            user_id=payload["user_id"],
            access_token=SecretValue.from_text(payload["access_token"]),
            refresh_token=SecretValue.from_text(payload["refresh_token"]),
            access_expires_at=expires_at,
        )


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    device_id: str
    public_key_b64: str

    def __post_init__(self) -> None:
        if not self.device_id.strip() or not self.public_key_b64.strip():
            raise ValueError("device identity fields cannot be empty")
