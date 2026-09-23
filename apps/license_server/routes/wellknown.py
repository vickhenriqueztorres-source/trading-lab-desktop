"""Well-known endpoints for public key discovery."""

from __future__ import annotations

from fastapi import APIRouter, Response

from apps.license_server.db import with_conn
from apps.license_server.keys import public_keys

router = APIRouter(tags=["well-known"])


@router.get("/.well-known/lease-keys")
def get_lease_keys(response: Response) -> dict[str, dict[str, str]]:
    """Return active public keys for client lease verification."""
    response.headers["Cache-Control"] = "public, max-age=3600"

    conn = None
    try:
        with with_conn() as active_conn:
            conn = active_conn
            keys = public_keys(conn)
    except Exception:
        # Fallback to in-memory active key if DB is unavailable
        keys = public_keys(None)

    return {"keys": keys}
