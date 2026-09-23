"""Device registration and challenge endpoints for Trading Lab License Server."""

from __future__ import annotations

import base64
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from apps.license_server.db import with_conn
from apps.license_server.dependencies import bearer_customer
from apps.license_server.errors import ApiError, ErrorCode
from apps.license_server.licensing import require_active_license

router = APIRouter(prefix="/api/v1/device", tags=["device"])


class RegisterDeviceRequest(BaseModel):
    device_id: str
    public_key_b64: str


class DeviceChallengeRequest(BaseModel):
    device_id: str


class DeviceChallengeResponse(BaseModel):
    challenge_id: str
    nonce_b64: str
    expires_at: str


@router.post("/register", status_code=204)
def register_device(
    payload: RegisterDeviceRequest,
    current_customer: Annotated[UUID, Depends(bearer_customer)],
) -> Response:
    """Register a new device identity or update last seen for an existing device."""
    device_id = payload.device_id.strip()
    if not device_id:
        raise ApiError(ErrorCode.AUTH_DEVICE_INVALID, "Missing device_id", http_status=400)

    # Validate Ed25519 public key (must decode to exactly 32 bytes)
    try:
        raw_pub = base64.b64decode(payload.public_key_b64, altchars=b"-_", validate=True)
        if len(raw_pub) != 32:
            raise ValueError("Public key must be exactly 32 bytes")
        Ed25519PublicKey.from_public_bytes(raw_pub)
    except Exception as exc:
        raise ApiError(
            ErrorCode.AUTH_DEVICE_INVALID,
            "Invalid device public key format",
            http_status=400,
        ) from exc

    with with_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT customer_id, public_key_b64, revoked
                FROM devices
                WHERE device_id = %s;
                """,
                (device_id,),
            )
            row = cur.fetchone()

        if row is not None:
            owner_id, stored_pub_b64, is_revoked = row
            if UUID(str(owner_id)) != current_customer:
                raise ApiError(
                    ErrorCode.AUTH_DEVICE_INVALID,
                    "Device belongs to another customer",
                    http_status=400,
                )

            try:
                stored_raw = base64.b64decode(stored_pub_b64, altchars=b"-_", validate=True)
            except Exception:
                stored_raw = b""

            if not hmac.compare_digest(stored_raw, raw_pub):
                raise ApiError(
                    ErrorCode.AUTH_DEVICE_INVALID,
                    "Device registered with different public key",
                    http_status=400,
                )

            if is_revoked:
                raise ApiError(
                    ErrorCode.AUTH_DEVICE_REVOKED,
                    "Device has been revoked",
                    http_status=403,
                )

            # Idempotent re-registration: update last_seen_at
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE devices SET last_seen_at = now() WHERE device_id = %s;",
                    (device_id,),
                )
            conn.commit()
            return Response(status_code=204)

        # New device: enforce active license and device limit
        license_row = require_active_license(conn, current_customer)

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)
                FROM devices
                WHERE customer_id = %s AND revoked = false;
                """,
                (current_customer,),
            )
            count_row = cur.fetchone()
            active_devices_count = int(count_row[0]) if count_row else 0

        if active_devices_count >= license_row.max_devices:
            raise ApiError(
                ErrorCode.AUTH_DEVICE_LIMIT,
                "Tu licencia ya está activa en otro equipo.",
                http_status=403,
            )

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO devices (
                    device_id, customer_id, public_key_b64, revoked, created_at, last_seen_at
                )
                VALUES (%s, %s, %s, false, now(), now());
                """,
                (device_id, current_customer, payload.public_key_b64),
            )
            cur.execute(
                """
                INSERT INTO audit_log (customer_id, actor, action, details)
                VALUES (%s, %s, 'device_registered', %s);
                """,
                (
                    current_customer,
                    str(current_customer),
                    json.dumps({"device_id": device_id}),
                ),
            )
        conn.commit()

        return Response(status_code=204)


@router.post("/challenge", response_model=DeviceChallengeResponse)
def create_device_challenge(
    payload: DeviceChallengeRequest,
    current_customer: Annotated[UUID, Depends(bearer_customer)],
) -> DeviceChallengeResponse:
    """Create a 32-byte proof-of-possession challenge for a registered device."""
    device_id = payload.device_id.strip()
    if not device_id:
        raise ApiError(ErrorCode.AUTH_DEVICE_INVALID, "Missing device_id", http_status=400)

    with with_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT customer_id, revoked FROM devices WHERE device_id = %s;",
                (device_id,),
            )
            row = cur.fetchone()

        if row is None:
            raise ApiError(ErrorCode.AUTH_DEVICE_INVALID, "Device not found", http_status=400)

        owner_id, is_revoked = row
        if UUID(str(owner_id)) != current_customer:
            raise ApiError(
                ErrorCode.AUTH_DEVICE_INVALID,
                "Device belongs to another customer",
                http_status=400,
            )

        if is_revoked:
            raise ApiError(
                ErrorCode.AUTH_DEVICE_REVOKED,
                "Device has been revoked",
                http_status=403,
            )

        nonce = secrets.token_bytes(32)
        nonce_b64 = base64.urlsafe_b64encode(nonce).decode("ascii")
        challenge_id = uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=2)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO device_challenges (
                    id, customer_id, device_id, nonce_b64, expires_at, consumed, created_at
                )
                VALUES (%s, %s, %s, %s, %s, false, now());
                """,
                (challenge_id, current_customer, device_id, nonce_b64, expires_at),
            )
        conn.commit()

        return DeviceChallengeResponse(
            challenge_id=str(challenge_id),
            nonce_b64=nonce_b64,
            expires_at=expires_at.isoformat(),
        )
