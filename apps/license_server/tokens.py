"""Token family issuance, rotation, and authentication for Trading Lab License Server."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from pydantic import BaseModel

from apps.license_server.errors import ApiError, ErrorCode


class TokenResponse(BaseModel):
    """Normalized response payload containing session tokens."""

    user_id: str
    access_token: str
    refresh_token: str
    access_expires_at: str


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_family(conn: Connection[Any], customer_id: UUID | str) -> TokenResponse:
    """Create a new token family, generating an access token and refresh token."""
    cid = UUID(str(customer_id))
    family_id = uuid4()
    now = datetime.now(UTC)

    access_plain = secrets.token_urlsafe(32)
    refresh_plain = secrets.token_urlsafe(48)

    access_digest = _hash_token(access_plain)
    refresh_digest = _hash_token(refresh_plain)

    access_expires_at = now + timedelta(minutes=10)
    refresh_expires_at = now + timedelta(days=30)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO api_tokens (
                token_digest, kind, customer_id, family_id, expires_at, used, revoked
            )
            VALUES (%s, 'access', %s, %s, %s, false, false);
            """,
            (access_digest, cid, family_id, access_expires_at),
        )
        cur.execute(
            """
            INSERT INTO api_tokens (
                token_digest, kind, customer_id, family_id, expires_at, used, revoked
            )
            VALUES (%s, 'refresh', %s, %s, %s, false, false);
            """,
            (refresh_digest, cid, family_id, refresh_expires_at),
        )

    return TokenResponse(
        user_id=str(cid),
        access_token=access_plain,
        refresh_token=refresh_plain,
        access_expires_at=access_expires_at.isoformat(),
    )


def rotate(conn: Connection[Any], refresh_token: str) -> TokenResponse:
    """Rotate an existing refresh token, issuing a new pair under the same family.

    If a used refresh token is presented, detects reuse and immediately revokes all tokens
    in the family, returning AUTH_REFRESH_REUSE.
    """
    token_str = refresh_token.strip()
    if not token_str:
        raise ApiError(ErrorCode.AUTH_TOKEN_INVALID, "Missing refresh token", http_status=401)

    digest = _hash_token(token_str)
    now = datetime.now(UTC)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT customer_id, family_id, expires_at, used, revoked
            FROM api_tokens
            WHERE token_digest = %s AND kind = 'refresh';
            """,
            (digest,),
        )
        row = cur.fetchone()

        if row is None:
            raise ApiError(ErrorCode.AUTH_TOKEN_INVALID, "Invalid refresh token", http_status=401)

        customer_id, family_id, expires_at, used, revoked = row
        cid = UUID(str(customer_id))
        fid = UUID(str(family_id))

        if revoked:
            raise ApiError(ErrorCode.AUTH_TOKEN_INVALID, "Revoked refresh token", http_status=401)

        if used:
            # Token reuse detected! Revoke entire family and commit immediately
            cur.execute(
                "UPDATE api_tokens SET revoked = true WHERE family_id = %s;",
                (fid,),
            )
            conn.commit()
            raise ApiError(
                ErrorCode.AUTH_REFRESH_REUSE,
                "Refresh token reuse detected",
                http_status=401,
            )

        if now >= expires_at:
            raise ApiError(ErrorCode.AUTH_TOKEN_INVALID, "Expired refresh token", http_status=401)

        # Mark presented refresh token as used
        cur.execute(
            "UPDATE api_tokens SET used = true WHERE token_digest = %s;",
            (digest,),
        )

        # Generate new pair
        new_access_plain = secrets.token_urlsafe(32)
        new_refresh_plain = secrets.token_urlsafe(48)

        new_access_digest = _hash_token(new_access_plain)
        new_refresh_digest = _hash_token(new_refresh_plain)

        new_access_expires_at = now + timedelta(minutes=10)
        new_refresh_expires_at = now + timedelta(days=30)

        cur.execute(
            """
            INSERT INTO api_tokens (
                token_digest, kind, customer_id, family_id, expires_at, used, revoked
            )
            VALUES (%s, 'access', %s, %s, %s, false, false);
            """,
            (new_access_digest, cid, fid, new_access_expires_at),
        )
        cur.execute(
            """
            INSERT INTO api_tokens (
                token_digest, kind, customer_id, family_id, expires_at, used, revoked
            )
            VALUES (%s, 'refresh', %s, %s, %s, false, false);
            """,
            (new_refresh_digest, cid, fid, new_refresh_expires_at),
        )

    return TokenResponse(
        user_id=str(cid),
        access_token=new_access_plain,
        refresh_token=new_refresh_plain,
        access_expires_at=new_access_expires_at.isoformat(),
    )


def authenticate_access(conn: Connection[Any], bearer: str) -> UUID:
    """Validate access token from Authorization header and return customer_id."""
    token = bearer.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    if not token:
        raise ApiError(ErrorCode.AUTH_TOKEN_INVALID, "Missing access token", http_status=401)

    digest = _hash_token(token)
    now = datetime.now(UTC)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT customer_id, expires_at, revoked
            FROM api_tokens
            WHERE token_digest = %s AND kind = 'access';
            """,
            (digest,),
        )
        row = cur.fetchone()

        if row is None:
            raise ApiError(ErrorCode.AUTH_TOKEN_INVALID, "Invalid access token", http_status=401)

        customer_id, expires_at, revoked = row
        if revoked:
            raise ApiError(ErrorCode.AUTH_TOKEN_INVALID, "Revoked access token", http_status=401)

        if now >= expires_at:
            raise ApiError(ErrorCode.AUTH_TOKEN_INVALID, "Expired access token", http_status=401)

        return UUID(str(customer_id))
