from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from packages.domain.models import require_aware_utc


class AuthorizationReason(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    AUTH_REQUIRED = "HG_AUTH_REQUIRED"
    LEASE_INVALID = "HG_LEASE_INVALID"
    LEASE_INVALID_SIGNATURE = "HG_LEASE_INVALID_SIGNATURE"
    LEASE_EXPIRED = "HG_LEASE_EXPIRED"
    LEASE_NOT_YET_VALID = "HG_LEASE_NOT_YET_VALID"
    LEASE_REVOKED = "HG_LEASE_REVOKED"
    DEVICE_REVOKED = "HG_DEVICE_REVOKED"
    DEVICE_MISMATCH = "HG_LEASE_DEVICE_MISMATCH"
    USER_MISMATCH = "HG_LEASE_USER_MISMATCH"
    ENTITLEMENT_MISSING = "HG_ENTITLEMENT_MISSING"
    CLIENT_INCOMPATIBLE = "HG_CLIENT_INCOMPATIBLE"
    REAL_MODE_DISABLED = "HG_REAL_MODE_DISABLED"
    AUTH_AGENT_UNAVAILABLE = "HG_AUTH_AGENT_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    new_entries_allowed: bool
    open_order_follow_up_allowed: bool
    reconciliation_allowed: bool
    reason: AuthorizationReason
    lease_id: str | None
    expires_at: datetime | None


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a non-empty string array")
    normalized = tuple(item.strip() for item in value)
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} contains invalid or duplicate values")
    return normalized


@dataclass(frozen=True, slots=True)
class LeaseClaims:
    format_version: int
    lease_id: str
    user_id: str
    device_id: str
    issued_at: datetime
    expires_at: datetime
    plan: str
    broker_access: tuple[str, ...]
    strategy_packs: tuple[str, ...]
    real_mode_allowed: bool
    client_version_min: str
    client_version_max: str
    nonce: str

    def __post_init__(self) -> None:
        if self.format_version != 1:
            raise ValueError("unsupported lease format")
        require_aware_utc(self.issued_at, "issued_at")
        require_aware_utc(self.expires_at, "expires_at")
        for field in (
            "lease_id",
            "user_id",
            "device_id",
            "plan",
            "client_version_min",
            "client_version_max",
            "nonce",
        ):
            if not getattr(self, field).strip():
                raise ValueError(f"{field} cannot be empty")
        if self.expires_at <= self.issued_at:
            raise ValueError("lease expiry must be after issuance")

    def to_payload(self) -> dict[str, object]:
        return {
            "broker_access": list(self.broker_access),
            "client_version_max": self.client_version_max,
            "client_version_min": self.client_version_min,
            "device_id": self.device_id,
            "expires_at": self.expires_at.isoformat(),
            "format_version": self.format_version,
            "issued_at": self.issued_at.isoformat(),
            "lease_id": self.lease_id,
            "nonce": self.nonce,
            "plan": self.plan,
            "real_mode_allowed": self.real_mode_allowed,
            "strategy_packs": list(self.strategy_packs),
            "user_id": self.user_id,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")

    @classmethod
    def from_external_payload(cls, payload: object) -> LeaseClaims:
        if not isinstance(payload, dict):
            raise ValueError("lease payload must be an object")
        required = {
            "broker_access",
            "client_version_max",
            "client_version_min",
            "device_id",
            "expires_at",
            "format_version",
            "issued_at",
            "lease_id",
            "nonce",
            "plan",
            "real_mode_allowed",
            "strategy_packs",
            "user_id",
        }
        if set(payload) != required:
            raise ValueError("lease payload schema is invalid")
        if type(payload["format_version"]) is not int:
            raise ValueError("format_version must be an integer")
        if type(payload["real_mode_allowed"]) is not bool:
            raise ValueError("real_mode_allowed must be a boolean")
        string_fields = (
            "client_version_max",
            "client_version_min",
            "device_id",
            "expires_at",
            "issued_at",
            "lease_id",
            "nonce",
            "plan",
            "user_id",
        )
        if not all(isinstance(payload[field], str) for field in string_fields):
            raise ValueError("lease string field has invalid type")
        try:
            issued_at = datetime.fromisoformat(payload["issued_at"])
            expires_at = datetime.fromisoformat(payload["expires_at"])
        except ValueError as exc:
            raise ValueError("lease timestamp is invalid") from exc
        return cls(
            format_version=payload["format_version"],
            lease_id=payload["lease_id"],
            user_id=payload["user_id"],
            device_id=payload["device_id"],
            issued_at=issued_at,
            expires_at=expires_at,
            plan=payload["plan"],
            broker_access=_string_tuple(payload["broker_access"], "broker_access"),
            strategy_packs=_string_tuple(payload["strategy_packs"], "strategy_packs"),
            real_mode_allowed=payload["real_mode_allowed"],
            client_version_min=payload["client_version_min"],
            client_version_max=payload["client_version_max"],
            nonce=payload["nonce"],
        )


@dataclass(frozen=True, slots=True, repr=False)
class SignedLease:
    key_id: str
    payload_b64: str
    signature_b64: str

    def __post_init__(self) -> None:
        if not self.key_id.strip() or not self.payload_b64 or not self.signature_b64:
            raise ValueError("signed lease fields cannot be empty")

    def __repr__(self) -> str:
        return f"SignedLease(key_id={self.key_id!r}, <redacted>)"

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "key_id": self.key_id,
                "payload_b64": self.payload_b64,
                "signature_b64": self.signature_b64,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def from_bytes(cls, value: bytes) -> SignedLease:
        try:
            payload = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("signed lease encoding is invalid") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "key_id",
            "payload_b64",
            "signature_b64",
        }:
            raise ValueError("signed lease schema is invalid")
        if not all(isinstance(item, str) for item in payload.values()):
            raise ValueError("signed lease fields must be strings")
        return cls(**payload)

    def payload_bytes(self) -> bytes:
        try:
            return base64.b64decode(self.payload_b64, altchars=b"-_", validate=True)
        except ValueError as exc:
            raise ValueError("lease payload encoding is invalid") from exc

    def signature_bytes(self) -> bytes:
        try:
            return base64.b64decode(self.signature_b64, altchars=b"-_", validate=True)
        except ValueError as exc:
            raise ValueError("lease signature encoding is invalid") from exc
