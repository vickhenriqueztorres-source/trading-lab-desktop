"""Admin session and CSRF signing helpers using itsdangerous."""

from __future__ import annotations

import secrets
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


def get_session_serializer(secret: str) -> URLSafeTimedSerializer:
    """Return timed serializer configured for admin session cookies."""
    return URLSafeTimedSerializer(secret_key=secret, salt="tl-admin-session")


def get_csrf_serializer(secret: str) -> URLSafeTimedSerializer:
    """Return timed serializer configured for admin CSRF tokens."""
    return URLSafeTimedSerializer(secret_key=secret, salt="tl-admin-csrf")


def sign_session(email: str, secret: str) -> str:
    """Sign an admin session token containing the authenticated email."""
    serializer = get_session_serializer(secret)
    return str(serializer.dumps({"email": email.strip().lower()}))


def verify_session(token: str, secret: str, max_age: int = 43200) -> str | None:
    """Verify an admin session token and return the email if valid and unexpired (default 12h)."""
    if not token:
        return None
    serializer = get_session_serializer(secret)
    try:
        data: dict[str, Any] = serializer.loads(token, max_age=max_age)
        email = data.get("email")
        if isinstance(email, str) and email:
            return email.strip().lower()
    except (BadSignature, SignatureExpired):
        return None
    return None


def sign_csrf(secret: str) -> str:
    """Generate a signed, single-use capable CSRF token."""
    serializer = get_csrf_serializer(secret)
    nonce = secrets.token_hex(16)
    return str(serializer.dumps({"csrf": nonce}))


def verify_csrf(token: str, secret: str, max_age: int = 7200) -> bool:
    """Verify that a CSRF token has valid signature and is within max_age (default 2h)."""
    if not token:
        return False
    serializer = get_csrf_serializer(secret)
    try:
        data: dict[str, Any] = serializer.loads(token, max_age=max_age)
        return bool(data.get("csrf"))
    except (BadSignature, SignatureExpired):
        return False
