"""Lease issuance and revocation check endpoints for Trading Lab License Server."""

from __future__ import annotations

import base64
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.license_server.db import with_conn
from apps.license_server.dependencies import bearer_customer
from apps.license_server.errors import ApiError, ErrorCode
from apps.license_server.keys import get_signer
from apps.license_server.licensing import require_active_license
from packages.licensing import LeaseClaims

router = APIRouter(prefix="/api/v1/lease", tags=["lease"])


class IssueLeaseRequest(BaseModel):
    device_id: str
    challenge_id: str
    signature_b64: str


class SignedLeaseResponse(BaseModel):
    key_id: str
    payload_b64: str
    signature_b64: str


class RevokedLeaseResponse(BaseModel):
    revoked: bool


@router.post("/issue", response_model=SignedLeaseResponse)
def issue_lease(
    payload: IssueLeaseRequest,
    current_customer: Annotated[UUID, Depends(bearer_customer)],
) -> SignedLeaseResponse:
    """Verify device possession and issue an Ed25519 signed license lease."""
    try:
        challenge_uuid = UUID(payload.challenge_id)
    except ValueError as exc:
        raise ApiError(
            ErrorCode.AUTH_DEVICE_PROOF_INVALID,
            "Invalid challenge_id format",
            http_status=400,
        ) from exc

    with with_conn() as conn:
        # 1. Load device challenge
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT customer_id, device_id, nonce_b64, expires_at, consumed
                FROM device_challenges
                WHERE id = %s;
                """,
                (challenge_uuid,),
            )
            challenge_row = cur.fetchone()

        if challenge_row is None or challenge_row[4]:  # None or consumed
            raise ApiError(
                ErrorCode.AUTH_DEVICE_PROOF_INVALID,
                "Device challenge not found or already consumed",
                http_status=400,
            )

        owner_id, ch_device_id, nonce_b64, expires_at, _consumed = challenge_row
        now = datetime.now(UTC)

        if UUID(str(owner_id)) != current_customer or ch_device_id != payload.device_id:
            raise ApiError(
                ErrorCode.AUTH_DEVICE_PROOF_INVALID,
                "Device challenge does not match customer or device",
                http_status=400,
            )

        if now >= expires_at:
            raise ApiError(
                ErrorCode.AUTH_DEVICE_PROOF_INVALID,
                "Device challenge has expired",
                http_status=400,
            )

        # 2. Load device public key
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public_key_b64, revoked FROM devices WHERE device_id = %s;",
                (payload.device_id,),
            )
            device_row = cur.fetchone()

        if device_row is None:
            raise ApiError(ErrorCode.AUTH_DEVICE_INVALID, "Device not found", http_status=400)

        pub_b64, is_revoked = device_row
        if is_revoked:
            raise ApiError(
                ErrorCode.AUTH_DEVICE_REVOKED,
                "Device has been revoked",
                http_status=403,
            )

        try:
            device_pub_bytes = base64.b64decode(pub_b64, altchars=b"-_", validate=True)
            device_pubkey = Ed25519PublicKey.from_public_bytes(device_pub_bytes)
            signature_bytes = base64.b64decode(payload.signature_b64, altchars=b"-_", validate=True)
            nonce_raw = base64.urlsafe_b64decode(nonce_b64)
            device_pubkey.verify(signature_bytes, nonce_raw)
        except (InvalidSignature, ValueError) as exc:
            raise ApiError(
                ErrorCode.AUTH_DEVICE_PROOF_INVALID,
                "Device signature verification failed",
                http_status=400,
            ) from exc

        # 3. Mark challenge as consumed
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE device_challenges SET consumed = true WHERE id = %s;",
                (challenge_uuid,),
            )
        conn.commit()

        # 4. Require active customer license
        license_row = require_active_license(conn, current_customer)

        # 5. Construct LeaseClaims according to PRD and hard limits
        ttl = timedelta(hours=24) if license_row.real_mode_allowed else timedelta(days=7)
        lease_expires_at = min(now + ttl, license_row.expires_at)
        if lease_expires_at <= now:
            raise ApiError(
                ErrorCode.AUTH_LICENSE_EXPIRED,
                "Tu acceso no está activo. Contacta soporte.",
                http_status=403,
            )

        lease_id = str(uuid4())
        claims = LeaseClaims(
            format_version=1,
            lease_id=lease_id,
            user_id=str(current_customer),
            device_id=payload.device_id,
            issued_at=now,
            expires_at=lease_expires_at,
            plan="TRADING_LAB_REAL" if license_row.real_mode_allowed else "PHASE0_PRACTICE",
            broker_access=tuple(license_row.broker_access),
            strategy_packs=tuple(license_row.strategy_packs),
            real_mode_allowed=license_row.real_mode_allowed,
            client_version_min="0.0.1",
            client_version_max="99.0.0",
            nonce=secrets.token_urlsafe(18),
        )

        # 6. Sign lease and record in leases table and audit_log
        signed = get_signer().sign(claims)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO leases (
                    lease_id, customer_id, device_id, issued_at, expires_at, revoked, created_at
                )
                VALUES (%s, %s, %s, %s, %s, false, now());
                """,
                (
                    UUID(lease_id),
                    current_customer,
                    payload.device_id,
                    claims.issued_at,
                    claims.expires_at,
                ),
            )
            cur.execute(
                """
                INSERT INTO audit_log (customer_id, actor, action, details)
                VALUES (%s, %s, 'lease_issued', %s);
                """,
                (
                    current_customer,
                    str(current_customer),
                    json.dumps({"lease_id": lease_id, "device_id": payload.device_id}),
                ),
            )
        conn.commit()

        return SignedLeaseResponse(
            key_id=signed.key_id,
            payload_b64=signed.payload_b64,
            signature_b64=signed.signature_b64,
        )


@router.get("/revoked/{lease_id}", response_model=RevokedLeaseResponse)
def check_lease_revoked(lease_id: str) -> RevokedLeaseResponse:
    """Check if a lease ID has been revoked (fails closed if malformed or unknown)."""
    try:
        lid = UUID(lease_id.strip())
    except ValueError:
        # Fails closed on malformed lease ID
        return RevokedLeaseResponse(revoked=True)

    with with_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT revoked FROM leases WHERE lease_id = %s;",
                (lid,),
            )
            row = cur.fetchone()

        if row is None:
            # Fails closed if lease does not exist
            return RevokedLeaseResponse(revoked=True)

        return RevokedLeaseResponse(revoked=bool(row[0]))
