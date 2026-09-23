"""FastAPI dependencies for Trading Lab License Server."""

from __future__ import annotations

from uuid import UUID

from fastapi import Header, HTTPException, Request, status

from apps.license_server.admin_auth import verify_session
from apps.license_server.db import with_conn
from apps.license_server.errors import ApiError, ErrorCode
from apps.license_server.settings import Settings, get_settings
from apps.license_server.tokens import authenticate_access


def bearer_customer(authorization: str | None = Header(None)) -> UUID:
    """Validate Bearer access token and return authenticated customer UUID."""
    if not authorization:
        raise ApiError(
            ErrorCode.AUTH_TOKEN_INVALID,
            "Missing Authorization header",
            http_status=401,
        )
    with with_conn() as conn:
        return authenticate_access(conn, authorization)


def require_admin(request: Request) -> str:
    """Validate admin session cookie and return authenticated admin email."""
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    cookie = request.cookies.get("admin_session")
    if not cookie:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"},
        )
    email = verify_session(cookie, settings.admin_session_secret)
    if not email or email != settings.admin_email.lower():
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"},
        )
    return email
