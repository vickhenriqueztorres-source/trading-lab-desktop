from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from packages.licensing.models import (
    AuthorizationDecision,
    AuthorizationReason,
    LeaseClaims,
    SignedLease,
)

_MAX_PRACTICE_LEASE = timedelta(days=7)
_MAX_REAL_LEASE = timedelta(hours=24)


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    if not parts or not all(part.isascii() and part.isdigit() for part in parts):
        raise ValueError("client version must be numeric dotted notation")
    return tuple(int(part) for part in parts)


class LeaseSigner:
    """Server-side helper. No signing key is constructed or embedded by the verifier."""

    def __init__(self, key_id: str, private_key: Ed25519PrivateKey) -> None:
        if not key_id.strip():
            raise ValueError("key_id cannot be empty")
        self.key_id = key_id
        self._private_key = private_key

    def sign(self, claims: LeaseClaims) -> SignedLease:
        payload = claims.canonical_bytes()
        signature = self._private_key.sign(payload)
        return SignedLease(
            key_id=self.key_id,
            payload_b64=base64.urlsafe_b64encode(payload).decode("ascii"),
            signature_b64=base64.urlsafe_b64encode(signature).decode("ascii"),
        )


class LeaseVerifier:
    def __init__(self, public_keys: dict[str, bytes]) -> None:
        if not public_keys:
            raise ValueError("at least one verification key is required")
        self._public_keys = {
            key_id: Ed25519PublicKey.from_public_bytes(value)
            for key_id, value in public_keys.items()
        }

    def verify(self, signed: SignedLease) -> LeaseClaims:
        public_key = self._public_keys.get(signed.key_id)
        if public_key is None:
            raise ValueError(AuthorizationReason.LEASE_INVALID_SIGNATURE.value)
        try:
            payload = signed.payload_bytes()
            public_key.verify(signed.signature_bytes(), payload)
        except (InvalidSignature, ValueError) as exc:
            raise ValueError(AuthorizationReason.LEASE_INVALID_SIGNATURE.value) from exc
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(AuthorizationReason.LEASE_INVALID.value) from exc
        claims = LeaseClaims.from_external_payload(decoded)
        if (
            not claims.real_mode_allowed
            and claims.expires_at - claims.issued_at > _MAX_PRACTICE_LEASE
        ):
            raise ValueError(AuthorizationReason.LEASE_INVALID.value)
        if claims.real_mode_allowed and claims.expires_at - claims.issued_at > _MAX_REAL_LEASE:
            raise ValueError(AuthorizationReason.LEASE_INVALID.value)
        return claims

    def evaluate(
        self,
        signed: SignedLease,
        *,
        now: datetime,
        expected_user_id: str,
        expected_device_id: str,
        client_version: str,
        broker: str,
        strategy_pack: str,
        real_mode: bool = False,
        revoked_lease_ids: frozenset[str] = frozenset(),
        device_revoked: bool = False,
    ) -> AuthorizationDecision:
        try:
            claims = self.verify(signed)
        except ValueError as exc:
            verification_reason = (
                AuthorizationReason.LEASE_INVALID_SIGNATURE
                if str(exc) == AuthorizationReason.LEASE_INVALID_SIGNATURE.value
                else AuthorizationReason.LEASE_INVALID
            )
            return self._blocked(verification_reason)
        reason: AuthorizationReason | None = None
        if claims.user_id != expected_user_id:
            reason = AuthorizationReason.USER_MISMATCH
        elif claims.device_id != expected_device_id:
            reason = AuthorizationReason.DEVICE_MISMATCH
        elif device_revoked:
            reason = AuthorizationReason.DEVICE_REVOKED
        elif claims.lease_id in revoked_lease_ids:
            reason = AuthorizationReason.LEASE_REVOKED
        elif now < claims.issued_at:
            reason = AuthorizationReason.LEASE_NOT_YET_VALID
        elif now >= claims.expires_at:
            reason = AuthorizationReason.LEASE_EXPIRED
        elif not (
            _version_tuple(claims.client_version_min)
            <= _version_tuple(client_version)
            <= _version_tuple(claims.client_version_max)
        ):
            reason = AuthorizationReason.CLIENT_INCOMPATIBLE
        elif broker not in claims.broker_access or strategy_pack not in claims.strategy_packs:
            reason = AuthorizationReason.ENTITLEMENT_MISSING
        elif real_mode and not claims.real_mode_allowed:
            reason = AuthorizationReason.REAL_MODE_DISABLED
        if reason is not None:
            return self._blocked(reason, claims)
        return AuthorizationDecision(
            new_entries_allowed=True,
            open_order_follow_up_allowed=True,
            reconciliation_allowed=True,
            reason=AuthorizationReason.AUTHORIZED,
            lease_id=claims.lease_id,
            expires_at=claims.expires_at,
        )

    @staticmethod
    def _blocked(
        reason: AuthorizationReason,
        claims: LeaseClaims | None = None,
    ) -> AuthorizationDecision:
        return AuthorizationDecision(
            new_entries_allowed=False,
            open_order_follow_up_allowed=True,
            reconciliation_allowed=True,
            reason=reason,
            lease_id=None if claims is None else claims.lease_id,
            expires_at=None if claims is None else claims.expires_at,
        )
