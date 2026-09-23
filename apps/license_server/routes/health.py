"""Health check endpoints for Trading Lab License Server."""

from __future__ import annotations

from fastapi import APIRouter

from apps.license_server.db import with_conn
from apps.license_server.errors import ApiError, ErrorCode

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, bool]:
    """Health check endpoint performing database ping."""
    try:
        with with_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(
            ErrorCode.AUTH_SERVICE_UNAVAILABLE,
            "Database health check failed",
        ) from exc
    return {"ok": True}
