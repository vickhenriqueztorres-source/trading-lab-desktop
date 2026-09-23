"""API Error codes, exceptions, and handlers for Trading Lab License Server."""

from __future__ import annotations

from enum import StrEnum

from fastapi import Request
from fastapi.responses import JSONResponse


class ErrorCode(StrEnum):
    AUTH_SERVICE_UNAVAILABLE = "AUTH_SERVICE_UNAVAILABLE"
    AUTH_CHALLENGE_INVALID = "AUTH_CHALLENGE_INVALID"
    AUTH_CHALLENGE_EXPIRED = "AUTH_CHALLENGE_EXPIRED"
    AUTH_OTP_INVALID = "AUTH_OTP_INVALID"
    AUTH_PKCE_INVALID = "AUTH_PKCE_INVALID"
    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    AUTH_REFRESH_REUSE = "AUTH_REFRESH_REUSE"
    AUTH_DEVICE_INVALID = "AUTH_DEVICE_INVALID"
    AUTH_DEVICE_REVOKED = "AUTH_DEVICE_REVOKED"
    AUTH_DEVICE_PROOF_INVALID = "AUTH_DEVICE_PROOF_INVALID"
    AUTH_LICENSE_EXPIRED = "AUTH_LICENSE_EXPIRED"
    AUTH_DEVICE_LIMIT = "AUTH_DEVICE_LIMIT"
    RATE_LIMITED = "RATE_LIMITED"


ERROR_STATUS_MAP: dict[ErrorCode, int] = {
    ErrorCode.AUTH_SERVICE_UNAVAILABLE: 503,
    ErrorCode.AUTH_CHALLENGE_INVALID: 400,
    ErrorCode.AUTH_CHALLENGE_EXPIRED: 400,
    ErrorCode.AUTH_OTP_INVALID: 401,
    ErrorCode.AUTH_PKCE_INVALID: 400,
    ErrorCode.AUTH_TOKEN_INVALID: 401,
    ErrorCode.AUTH_REFRESH_REUSE: 401,
    ErrorCode.AUTH_DEVICE_INVALID: 400,
    ErrorCode.AUTH_DEVICE_REVOKED: 403,
    ErrorCode.AUTH_DEVICE_PROOF_INVALID: 400,
    ErrorCode.AUTH_LICENSE_EXPIRED: 403,
    ErrorCode.AUTH_DEVICE_LIMIT: 403,
    ErrorCode.RATE_LIMITED: 429,
}


class ApiError(Exception):
    """Normalized API error producing standard JSON error envelopes."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        http_status: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
        default_status = ERROR_STATUS_MAP.get(code, 400)
        self.http_status = http_status if http_status is not None else default_status
        self.headers = headers or {}


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    """FastAPI exception handler for normalized ApiError."""
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {"code": exc.code.value, "message": exc.message}},
        headers=exc.headers,
    )


async def unhandled_exception_handler(_request: Request, _exc: Exception) -> JSONResponse:
    """FastAPI exception handler for unhandled exceptions (fails closed without leaking details)."""
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": ErrorCode.AUTH_SERVICE_UNAVAILABLE.value,
                "message": "Service is temporarily unavailable",
            }
        },
    )
