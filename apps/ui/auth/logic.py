from __future__ import annotations

import re

_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9]+(?:[-.][a-zA-Z0-9]+)*\.[a-zA-Z0-9-.]+$")

_AUTH_ERROR_MAP: dict[str, str] = {
    "AUTH_OTP_INVALID": "login.err_otp_invalid",
    "AUTH_CHALLENGE_EXPIRED": "login.err_expired",
    "AUTH_LICENSE_EXPIRED": "login.err_license_expired",
    "AUTH_DEVICE_LIMIT": "login.err_device_limit",
    "AUTH_SERVICE_UNAVAILABLE": "login.err_unavailable",
    "AUTH_RATE_LIMITED": "login.err_rate_limited",
    "AUTH_NETWORK_ERROR": "login.err_unavailable",
}


def mask_email(email: str | None) -> str:
    """Mask email for privacy, e.g. 'julio.cesar@gmail.com' -> 'ju***@gmail.com'."""
    if not email or not isinstance(email, str):
        return "***"
    normalized = email.strip()
    if "@" not in normalized:
        return f"{normalized[:2]}***" if len(normalized) >= 2 else "***"
    user, domain = normalized.split("@", 1)
    masked_user = f"{user[0]}***" if len(user) <= 2 else f"{user[:2]}***"
    return f"{masked_user}@{domain}"


def validate_email(email: str | None) -> bool:
    """Validate email syntax."""
    if not email or not isinstance(email, str):
        return False
    normalized = email.strip()
    if len(normalized) > 254:
        return False
    return bool(_EMAIL_PATTERN.match(normalized))


def format_code(digits: list[str]) -> str:
    """Assemble 6 digits into a single code string."""
    return "".join(d.strip() for d in digits if d and d.strip().isalnum())


def map_auth_error_key(reason_code: str | None) -> str:
    """Map AuthAgent / Core reason code to an i18n key."""
    if not reason_code:
        return "login.err_unavailable"
    return _AUTH_ERROR_MAP.get(reason_code.strip(), "login.err_unavailable")


def format_countdown(seconds_remaining: int) -> str:
    """Format seconds into MM:SS string."""
    clamped = max(0, seconds_remaining)
    minutes = clamped // 60
    seconds = clamped % 60
    return f"{minutes:02d}:{seconds:02d}"
