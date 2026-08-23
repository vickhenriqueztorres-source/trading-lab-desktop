from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit

from apps.deriv_worker.schema import DerivErrorCategory, DerivWorkerError

DERIV_HOST = "api.derivws.com"
PUBLIC_WS_PATH = "/trading/v1/options/ws/public"
DEMO_WS_PATH = "/trading/v1/options/ws/demo"
REAL_WS_PATH = "/trading/v1/options/ws/real"
PUBLIC_WS_URL = f"wss://{DERIV_HOST}{PUBLIC_WS_PATH}"

FORBIDDEN_OPERATIONS_DENYLIST = frozenset(
    {
        "auto_start",
        "bulk_purchase",
        "cashier",
        "deposit",
        "withdraw",
        "withdrawal",
    }
)

TRADING_OPERATION_DENYLIST = (
    frozenset(
        {
            "buy",
            "cancel",
            "contract_update",
            "proposal",
            "sell",
        }
    )
    | FORBIDDEN_OPERATIONS_DENYLIST
)


def validate_deriv_ws_url(url: str, *, expected_demo: bool | None = None) -> str:
    """Accept only the official public or OTP-authenticated demo endpoint."""

    parsed = urlsplit(url)
    if (
        parsed.scheme != "wss"
        or parsed.hostname != DERIV_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_WS_HOST_FORBIDDEN",
        )
    if parsed.path == REAL_WS_PATH or "/real" in parsed.path:
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_REAL_WS_FORBIDDEN",
        )
    is_demo = parsed.path == DEMO_WS_PATH
    is_public = parsed.path == PUBLIC_WS_PATH
    if not is_demo and not is_public:
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_WS_PATH_FORBIDDEN",
        )
    if expected_demo is not None and is_demo is not expected_demo:
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_WS_PATH_FORBIDDEN",
        )
    if is_demo:
        try:
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise DerivWorkerError(
                DerivErrorCategory.AUTH_FAILED,
                "DERIV_DEMO_OTP_MISSING",
            ) from exc
        if set(query) != {"otp"} or len(query["otp"]) != 1 or not query["otp"][0]:
            raise DerivWorkerError(
                DerivErrorCategory.AUTH_FAILED,
                "DERIV_DEMO_OTP_MISSING",
            )
    elif parsed.query:
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_PUBLIC_WS_QUERY_FORBIDDEN",
        )
    return url


def validate_deriv_account(account_payload: Mapping[str, object]) -> None:
    account_type = account_payload.get("account_type")
    if account_type != "demo":
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_REAL_ACCOUNT_FORBIDDEN",
        )


def validate_outbound_deriv_request(
    opcode: str,
    payload: Mapping[str, object],
    *,
    demo_authenticated: bool = False,
) -> None:
    if opcode in FORBIDDEN_OPERATIONS_DENYLIST or any(
        key in FORBIDDEN_OPERATIONS_DENYLIST for key in payload
    ):
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_TRADING_OPERATION_DISABLED",
        )
    if not demo_authenticated and (
        opcode in TRADING_OPERATION_DENYLIST
        or any(key in TRADING_OPERATION_DENYLIST for key in payload)
    ):
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_TRADING_OPERATION_DISABLED",
        )
