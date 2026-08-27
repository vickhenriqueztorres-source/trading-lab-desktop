from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from packages.identity import OtpCode, PkceMaterial, normalize_email
from packages.licensing import LeaseClaims, LeaseSigner


class FakeIdentityServiceErrorCode(StrEnum):
    UNAVAILABLE = "AUTH_SERVICE_UNAVAILABLE"
    CHALLENGE_INVALID = "AUTH_CHALLENGE_INVALID"
    CHALLENGE_EXPIRED = "AUTH_CHALLENGE_EXPIRED"
    OTP_INVALID = "AUTH_OTP_INVALID"
    PKCE_INVALID = "AUTH_PKCE_INVALID"
    TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    TOKEN_REUSE = "AUTH_REFRESH_REUSE"
    DEVICE_INVALID = "AUTH_DEVICE_INVALID"
    DEVICE_REVOKED = "AUTH_DEVICE_REVOKED"
    DEVICE_PROOF_INVALID = "AUTH_DEVICE_PROOF_INVALID"


class FakeIdentityServiceError(RuntimeError):
    def __init__(self, code: FakeIdentityServiceErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(slots=True)
class _LoginRecord:
    email: str
    pkce_challenge: str
    otp_digest: bytes
    expires_at: datetime
    consumed: bool = False


@dataclass(slots=True)
class _TokenFamily:
    user_id: str
    active_refresh_digest: bytes
    revoked: bool = False


@dataclass(slots=True)
class _AccessRecord:
    user_id: str
    expires_at: datetime


@dataclass(slots=True)
class _DeviceRecord:
    user_id: str
    public_key: Ed25519PublicKey
    revoked: bool = False


@dataclass(slots=True)
class _DeviceChallenge:
    user_id: str
    device_id: str
    nonce: bytes
    expires_at: datetime
    consumed: bool = False


class FakeIdentityService:
    """Deterministic-contract, in-memory identity/licensing control-plane simulator."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        lease_ttl: timedelta = timedelta(days=7),
        strategy_packs: tuple[str, ...] = ("strategy-test",),
        signing_key_id: str = "phase0-ed25519-1",
        otp_factory: Callable[[], OtpCode] | None = None,
        real_mode_allowed: bool = False,
    ) -> None:
        maximum_ttl = timedelta(hours=24) if real_mode_allowed else timedelta(days=7)
        if lease_ttl <= timedelta(0) or lease_ttl > maximum_ttl:
            raise ValueError("practice lease_ttl must be within seven days")
        self._now = now or (lambda: datetime.now(UTC))
        self._lease_ttl = lease_ttl
        self._real_mode_allowed = real_mode_allowed
        self._strategy_packs = strategy_packs
        if not signing_key_id.strip() or len(signing_key_id) > 128:
            raise ValueError("signing_key_id is invalid")
        self._otp_factory = otp_factory
        self._available = True
        self._users: dict[str, str] = {}
        self._logins: dict[str, _LoginRecord] = {}
        self._deliveries: dict[str, OtpCode] = {}
        self._families: dict[str, _TokenFamily] = {}
        self._refresh_to_family: dict[bytes, str] = {}
        self._used_refresh_to_family: dict[bytes, str] = {}
        self._access: dict[bytes, _AccessRecord] = {}
        self._devices: dict[str, _DeviceRecord] = {}
        self._device_challenges: dict[str, _DeviceChallenge] = {}
        self._revoked_leases: set[str] = set()
        signing_key = Ed25519PrivateKey.generate()
        self._lease_signer = LeaseSigner(signing_key_id, signing_key)
        self._lease_public_key = signing_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    @property
    def lease_verification_keys(self) -> dict[str, bytes]:
        return {self._lease_signer.key_id: self._lease_public_key}

    def set_available(self, available: bool) -> None:
        self._available = available

    def start_login(self, email: str, pkce_challenge: str) -> dict[str, object]:
        self._ensure_available()
        normalized_email = normalize_email(email)
        if not pkce_challenge.strip():
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.PKCE_INVALID)
        challenge_id = str(uuid4())
        code = (
            self._otp_factory()
            if self._otp_factory is not None
            else OtpCode(f"{secrets.randbelow(1_000_000):06d}")
        )
        expires_at = self._now() + timedelta(minutes=5)
        self._logins[challenge_id] = _LoginRecord(
            email=normalized_email,
            pkce_challenge=pkce_challenge,
            otp_digest=self._digest(code.value),
            expires_at=expires_at,
        )
        self._deliveries[challenge_id] = code
        return {"challenge_id": challenge_id, "expires_at": expires_at.isoformat()}

    def take_otp_for_testing(self, challenge_id: str) -> OtpCode:
        """Fake delivery adapter; returned code is generated, never a fixture literal."""
        try:
            return self._deliveries.pop(challenge_id)
        except KeyError as exc:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.CHALLENGE_INVALID) from exc

    def complete_login(
        self,
        challenge_id: str,
        code: OtpCode,
        pkce_verifier: str,
    ) -> dict[str, object]:
        self._ensure_available()
        record = self._logins.get(challenge_id)
        if record is None or record.consumed:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.CHALLENGE_INVALID)
        if self._now() >= record.expires_at:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.CHALLENGE_EXPIRED)
        if not hmac.compare_digest(record.otp_digest, self._digest(code.value)):
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.OTP_INVALID)
        if not hmac.compare_digest(
            record.pkce_challenge,
            PkceMaterial.challenge_for(pkce_verifier),
        ):
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.PKCE_INVALID)
        record.consumed = True
        self._deliveries.pop(challenge_id, None)
        user_id = self._users.setdefault(record.email, str(uuid4()))
        return self._new_token_family(user_id)

    def refresh_session(self, refresh_token: str) -> dict[str, object]:
        self._ensure_available()
        digest = self._digest(refresh_token)
        reused_family_id = self._used_refresh_to_family.get(digest)
        if reused_family_id is not None:
            self._families[reused_family_id].revoked = True
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.TOKEN_REUSE)
        family_id = self._refresh_to_family.get(digest)
        if family_id is None:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.TOKEN_INVALID)
        family = self._families[family_id]
        if family.revoked or not hmac.compare_digest(family.active_refresh_digest, digest):
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.TOKEN_INVALID)
        self._refresh_to_family.pop(digest, None)
        self._used_refresh_to_family[digest] = family_id
        response = self._token_response(family.user_id)
        new_refresh = response["refresh_token"]
        assert isinstance(new_refresh, str)
        new_digest = self._digest(new_refresh)
        family.active_refresh_digest = new_digest
        self._refresh_to_family[new_digest] = family_id
        return response

    def register_device(
        self,
        access_token: str,
        device_id: str,
        public_key_b64: str,
    ) -> None:
        user_id = self._authenticate_access(access_token)
        try:
            public_raw = base64.b64decode(public_key_b64, altchars=b"-_", validate=True)
            public_key = Ed25519PublicKey.from_public_bytes(public_raw)
        except ValueError as exc:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.DEVICE_INVALID) from exc
        current = self._devices.get(device_id)
        if current is not None:
            current_raw = current.public_key.public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            if current.user_id != user_id or not hmac.compare_digest(current_raw, public_raw):
                raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.DEVICE_INVALID)
            if current.revoked:
                raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.DEVICE_REVOKED)
            return
        self._devices[device_id] = _DeviceRecord(user_id, public_key)

    def create_device_challenge(self, access_token: str, device_id: str) -> dict[str, object]:
        user_id = self._authenticate_access(access_token)
        device = self._require_device(user_id, device_id)
        if device.revoked:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.DEVICE_REVOKED)
        challenge_id = str(uuid4())
        nonce = secrets.token_bytes(32)
        expires_at = self._now() + timedelta(minutes=2)
        self._device_challenges[challenge_id] = _DeviceChallenge(
            user_id=user_id,
            device_id=device_id,
            nonce=nonce,
            expires_at=expires_at,
        )
        return {
            "challenge_id": challenge_id,
            "nonce_b64": base64.urlsafe_b64encode(nonce).decode("ascii"),
            "expires_at": expires_at.isoformat(),
        }

    def issue_lease(
        self,
        access_token: str,
        device_id: str,
        challenge_id: str,
        signature_b64: str,
    ) -> dict[str, object]:
        user_id = self._authenticate_access(access_token)
        device = self._require_device(user_id, device_id)
        challenge = self._device_challenges.get(challenge_id)
        if (
            challenge is None
            or challenge.consumed
            or challenge.user_id != user_id
            or challenge.device_id != device_id
            or self._now() >= challenge.expires_at
        ):
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.DEVICE_PROOF_INVALID)
        try:
            signature = base64.b64decode(signature_b64, altchars=b"-_", validate=True)
            device.public_key.verify(signature, challenge.nonce)
        except (InvalidSignature, ValueError) as exc:
            raise FakeIdentityServiceError(
                FakeIdentityServiceErrorCode.DEVICE_PROOF_INVALID
            ) from exc
        challenge.consumed = True
        now = self._now()
        claims = LeaseClaims(
            format_version=1,
            lease_id=str(uuid4()),
            user_id=user_id,
            device_id=device_id,
            issued_at=now,
            expires_at=now + self._lease_ttl,
            plan="TRADING_LAB_REAL" if self._real_mode_allowed else "PHASE0_PRACTICE",
            broker_access=("DERIV", "IQ_OPTION"),
            strategy_packs=self._strategy_packs,
            real_mode_allowed=self._real_mode_allowed,
            client_version_min="0.0.1",
            client_version_max="0.0.1",
            nonce=secrets.token_urlsafe(18),
        )
        signed = self._lease_signer.sign(claims)
        return {
            "key_id": signed.key_id,
            "payload_b64": signed.payload_b64,
            "signature_b64": signed.signature_b64,
        }

    def revoke_device(self, device_id: str) -> None:
        device = self._devices.get(device_id)
        if device is not None:
            device.revoked = True

    def revoke_lease(self, lease_id: str) -> None:
        self._revoked_leases.add(lease_id)

    def is_lease_revoked(self, lease_id: str) -> bool:
        self._ensure_available()
        return lease_id in self._revoked_leases

    def _new_token_family(self, user_id: str) -> dict[str, object]:
        family_id = str(uuid4())
        response = self._token_response(user_id)
        refresh_token = response["refresh_token"]
        assert isinstance(refresh_token, str)
        refresh_digest = self._digest(refresh_token)
        self._families[family_id] = _TokenFamily(user_id, refresh_digest)
        self._refresh_to_family[refresh_digest] = family_id
        return response

    def _token_response(self, user_id: str) -> dict[str, object]:
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(48)
        expires_at = self._now() + timedelta(minutes=10)
        self._access[self._digest(access_token)] = _AccessRecord(user_id, expires_at)
        return {
            "user_id": user_id,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "access_expires_at": expires_at.isoformat(),
        }

    def _authenticate_access(self, access_token: str) -> str:
        self._ensure_available()
        record = self._access.get(self._digest(access_token))
        if record is None or self._now() >= record.expires_at:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.TOKEN_INVALID)
        return record.user_id

    def _require_device(self, user_id: str, device_id: str) -> _DeviceRecord:
        device = self._devices.get(device_id)
        if device is None or device.user_id != user_id:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.DEVICE_INVALID)
        if device.revoked:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.DEVICE_REVOKED)
        return device

    def _ensure_available(self) -> None:
        if not self._available:
            raise FakeIdentityServiceError(FakeIdentityServiceErrorCode.UNAVAILABLE)

    @staticmethod
    def _digest(value: str) -> bytes:
        return hashlib.sha256(value.encode("utf-8")).digest()
