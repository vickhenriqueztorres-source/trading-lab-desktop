"""Authentication endpoints for Trading Lab License Server."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel

from apps.license_server.cleanup import maybe_purge_expired
from apps.license_server.db import with_conn
from apps.license_server.errors import ApiError, ErrorCode
from apps.license_server.licensing import require_active_license
from apps.license_server.mail import get_mail_provider
from apps.license_server.ratelimit import get_rate_limiter
from apps.license_server.settings import get_settings
from apps.license_server.tokens import TokenResponse, issue_family, rotate
from packages.identity import PkceMaterial, normalize_email

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_PKCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")


class StartLoginRequest(BaseModel):
    email: str
    pkce_challenge: str


class StartLoginResponse(BaseModel):
    challenge_id: str
    expires_at: str


class VerifyRequest(BaseModel):
    challenge_id: str
    otp_code: str
    pkce_verifier: str


class RefreshRequest(BaseModel):
    refresh_token: str


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


@router.post("/start", response_model=StartLoginResponse)
def start_login(payload: StartLoginRequest, request: Request) -> StartLoginResponse:
    """Initiate email login: validates PKCE, applies rate limits, and sends OTP."""
    # 1. Validate PKCE challenge format (43-128 chars, urlsafe b64 without '=')
    if not payload.pkce_challenge or not _PKCE_PATTERN.fullmatch(payload.pkce_challenge):
        raise ApiError(ErrorCode.AUTH_PKCE_INVALID, "Invalid PKCE challenge format")

    # 2. Normalize and validate email address
    try:
        email = normalize_email(payload.email)
    except ValueError as exc:
        raise ApiError(ErrorCode.AUTH_CHALLENGE_INVALID, "Invalid email address format") from exc

    settings = getattr(request.app.state, "settings", None) or get_settings()
    limiter = get_rate_limiter()

    # 3. Rate limiting (3/10min per email, 10/10min per IP)
    allowed, retry_after = limiter.check(f"email:{email}", max_requests=3, window_seconds=600.0)
    if not allowed:
        raise ApiError(
            ErrorCode.RATE_LIMITED,
            "Too many login attempts for this email. Please try again later.",
            http_status=429,
            headers={"Retry-After": str(retry_after)},
        )

    ip = _client_ip(request)
    allowed, retry_after = limiter.check(f"ip:{ip}", max_requests=10, window_seconds=600.0)
    if not allowed:
        raise ApiError(
            ErrorCode.RATE_LIMITED,
            "Too many login attempts from this IP. Please try again later.",
            http_status=429,
            headers={"Retry-After": str(retry_after)},
        )

    # 4. Generate OTP and hash
    code = f"{secrets.randbelow(1_000_000):06d}"
    code_digest = hashlib.sha256(code.encode("ascii")).hexdigest()
    challenge_id = str(uuid4())
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=5)

    code_plain = code if settings.mail_provider == "console" else None

    # 5. Persist to database
    with with_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO otp_challenges (
                    id, email, pkce_challenge, code_digest, code_plain,
                    delivery_status, attempts, expires_at, consumed
                )
                VALUES (%s, %s, %s, %s, %s, 'pending', 0, %s, false);
                """,
                (challenge_id, email, payload.pkce_challenge, code_digest, code_plain, expires_at),
            )
        conn.commit()

        # 6. Send email via configured provider
        provider = get_mail_provider(settings)
        try:
            provider.send_otp(email, code, 5, challenge_id=challenge_id)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE otp_challenges SET delivery_status = 'sent' WHERE id = %s;",
                    (challenge_id,),
                )
            conn.commit()
        except Exception as exc:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE otp_challenges SET delivery_status = 'failed' WHERE id = %s;",
                    (challenge_id,),
                )
            conn.commit()
            raise ApiError(
                ErrorCode.AUTH_SERVICE_UNAVAILABLE,
                "Unable to deliver OTP email",
            ) from exc

        # 7. Opportunistic cleanup
        maybe_purge_expired(conn)

    return StartLoginResponse(
        challenge_id=challenge_id,
        expires_at=expires_at.isoformat(),
    )


# Diagnostic and E2E testing hook (strictly guarded)
@router.get("/__test__/last-otp")
def get_last_test_otp(email: str) -> dict[str, str]:
    """Test hook only active when MAIL_PROVIDER=console and ENABLE_TEST_HOOKS=1."""
    settings = get_settings()
    enable_test_hooks = os.environ.get("ENABLE_TEST_HOOKS", "0").strip() == "1"
    if settings.environment != "development" and (
        settings.mail_provider != "console" or not enable_test_hooks
    ):
        raise ApiError(
            ErrorCode.AUTH_CHALLENGE_INVALID,
            "Test hooks are disabled",
            http_status=404,
        )

    try:
        norm_email = normalize_email(email)
    except ValueError as exc:
        raise ApiError(ErrorCode.AUTH_CHALLENGE_INVALID, "Invalid email") from exc

    with with_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT code_plain FROM otp_challenges
            WHERE email = %s AND code_plain IS NOT NULL
            ORDER BY created_at DESC LIMIT 1;
            """,
            (norm_email,),
        )
        row = cur.fetchone()
        if not row or not row[0]:
            raise ApiError(
                ErrorCode.AUTH_CHALLENGE_INVALID,
                "No OTP found for email",
                http_status=404,
            )
        return {"code": str(row[0])}


@router.post("/verify", response_model=TokenResponse)
def verify_login(payload: VerifyRequest) -> TokenResponse:
    """Validate OTP code and PKCE verifier, authenticate customer and issue tokens."""
    try:
        challenge_id = UUID(payload.challenge_id)
    except ValueError as exc:
        raise ApiError(
            ErrorCode.AUTH_CHALLENGE_INVALID,
            "Invalid challenge ID format",
            http_status=400,
        ) from exc

    with with_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT email, pkce_challenge, code_digest, attempts, expires_at, consumed
                FROM otp_challenges
                WHERE id = %s;
                """,
                (challenge_id,),
            )
            row = cur.fetchone()

        if row is None or row[5]:  # None or consumed
            raise ApiError(
                ErrorCode.AUTH_CHALLENGE_INVALID,
                "Challenge not found or already consumed",
                http_status=400,
            )

        email, pkce_challenge, code_digest, attempts, expires_at, _consumed = row
        now = datetime.now(UTC)

        # 1. Check expiration
        if now >= expires_at:
            raise ApiError(
                ErrorCode.AUTH_CHALLENGE_EXPIRED,
                "OTP challenge has expired",
                http_status=400,
            )

        # 2. Check maximum attempts
        if attempts >= 5:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE otp_challenges SET consumed = true WHERE id = %s;",
                    (challenge_id,),
                )
            conn.commit()
            raise ApiError(
                ErrorCode.AUTH_CHALLENGE_INVALID,
                "Maximum attempts exceeded for this challenge",
                http_status=400,
            )

        # 3. Verify OTP
        clean_code = payload.otp_code.strip()
        candidate_digest = hashlib.sha256(clean_code.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(candidate_digest, code_digest):
            new_attempts = attempts + 1
            is_consumed = new_attempts >= 5
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE otp_challenges SET attempts = %s, consumed = %s WHERE id = %s;",
                    (new_attempts, is_consumed, challenge_id),
                )
            conn.commit()
            raise ApiError(
                ErrorCode.AUTH_OTP_INVALID,
                "Invalid OTP code",
                http_status=401,
            )

        # 4. Verify PKCE
        try:
            derived_challenge = PkceMaterial.challenge_for(payload.pkce_verifier)
        except Exception as exc:
            raise ApiError(
                ErrorCode.AUTH_PKCE_INVALID,
                "Invalid PKCE verifier",
                http_status=400,
            ) from exc

        if not hmac.compare_digest(derived_challenge, pkce_challenge):
            raise ApiError(
                ErrorCode.AUTH_PKCE_INVALID,
                "PKCE verifier does not match challenge",
                http_status=400,
            )

        # 5. Mark challenge consumed
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE otp_challenges SET consumed = true WHERE id = %s;",
                (challenge_id,),
            )
        conn.commit()

        # 6. Upsert customer
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO customers (email)
                VALUES (%s)
                ON CONFLICT (email) DO UPDATE SET updated_at = now()
                RETURNING id;
                """,
                (email,),
            )
            cust_row = cur.fetchone()
            if not cust_row:
                raise ApiError(
                    ErrorCode.AUTH_SERVICE_UNAVAILABLE,
                    "Failed to resolve customer record",
                )
            customer_id = UUID(str(cust_row[0]))
        conn.commit()

        # 7. Require active license
        require_active_license(conn, customer_id)

        # 8. Record audit log
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log (customer_id, actor, action, details)
                VALUES (%s, %s, 'login', %s);
                """,
                (customer_id, email, json.dumps({"challenge_id": str(challenge_id)})),
            )

        # 9. Issue session tokens
        tokens = issue_family(conn, customer_id)
        conn.commit()

        return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh_session(payload: RefreshRequest) -> TokenResponse:
    """Rotate an existing refresh token and issue a new token pair."""
    with with_conn() as conn:
        tokens = rotate(conn, payload.refresh_token)
        require_active_license(conn, UUID(tokens.user_id))
        conn.commit()
        return tokens
