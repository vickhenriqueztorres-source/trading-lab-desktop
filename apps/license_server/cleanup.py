"""Opportunistic database cleanup routines for Trading Lab License Server."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from psycopg import Connection

logger = logging.getLogger("license_server.cleanup")


def purge_expired(conn: Connection[Any]) -> tuple[int, int]:
    """Purge otp_challenges and device_challenges expired more than 1 hour ago."""
    deleted_otp = 0
    deleted_device = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM otp_challenges
                WHERE expires_at < now() - INTERVAL '1 hour';
                """
            )
            deleted_otp = cur.rowcount

            cur.execute(
                """
                DELETE FROM device_challenges
                WHERE expires_at < now() - INTERVAL '1 hour';
                """
            )
            deleted_device = cur.rowcount
        conn.commit()
        if deleted_otp > 0 or deleted_device > 0:
            logger.info(
                "Purged expired challenges: %d OTPs, %d device nonces",
                deleted_otp,
                deleted_device,
            )
    except Exception as exc:
        logger.warning("Error during opportunistic cleanup: %s", exc)
        conn.rollback()

    return deleted_otp, deleted_device


def maybe_purge_expired(conn: Connection[Any], *, probability: int = 20) -> None:
    """Trigger cleanup probabilistically (default: 1 in 20 requests)."""
    if secrets.randbelow(probability) == 0:
        purge_expired(conn)
