"""Customer license verification and retrieval for Trading Lab License Server."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg import Connection

from apps.license_server.errors import ApiError, ErrorCode


@dataclass(frozen=True, slots=True)
class LicenseRow:
    """Immutable representation of a customer license record."""

    id: UUID
    customer_id: UUID
    plan: str
    status: str
    broker_access: tuple[str, ...]
    strategy_packs: tuple[str, ...]
    real_mode_allowed: bool
    max_devices: int
    starts_at: datetime
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


def active_license(conn: Connection[Any], customer_id: UUID | str) -> LicenseRow | None:
    """Return the most recently expiring active license for the customer, or None."""
    cid = UUID(str(customer_id))
    now = datetime.now(UTC)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, customer_id, plan, status, broker_access, strategy_packs,
                   real_mode_allowed, max_devices, starts_at, expires_at, created_at, updated_at
            FROM licenses
            WHERE customer_id = %s
              AND status = 'active'
              AND starts_at <= %s
              AND expires_at > %s
            ORDER BY expires_at DESC
            LIMIT 1;
            """,
            (cid, now, now),
        )
        row = cur.fetchone()
        if row is None:
            return None

        (
            lic_id,
            cust_id,
            plan,
            status,
            broker_access,
            strategy_packs,
            real_mode_allowed,
            max_devices,
            starts_at,
            expires_at,
            created_at,
            updated_at,
        ) = row

        return LicenseRow(
            id=UUID(str(lic_id)),
            customer_id=UUID(str(cust_id)),
            plan=str(plan),
            status=str(status),
            broker_access=tuple(str(b) for b in (broker_access or ())),
            strategy_packs=tuple(str(s) for s in (strategy_packs or ())),
            real_mode_allowed=bool(real_mode_allowed),
            max_devices=int(max_devices),
            starts_at=starts_at,
            expires_at=expires_at,
            created_at=created_at,
            updated_at=updated_at,
        )


def require_active_license(conn: Connection[Any], customer_id: UUID | str) -> LicenseRow:
    """Ensure customer has an active, unexpired license; raise 403 AUTH_LICENSE_EXPIRED if not."""
    lic = active_license(conn, customer_id)
    if lic is None:
        raise ApiError(
            ErrorCode.AUTH_LICENSE_EXPIRED,
            "Tu acceso no está activo. Contacta soporte.",
            http_status=403,
        )
    return lic
