from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
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

READ_ONLY_PROPOSAL_CONTRACT_TYPES = frozenset(
    {
        "CALL",
        "PUT",
        "DIGITDIFF",
        "DIGITEVEN",
        "DIGITMATCH",
        "DIGITODD",
        "DIGITOVER",
        "DIGITUNDER",
    }
)
_READ_ONLY_PROPOSAL_ALLOWED_KEYS = frozenset(
    {
        "amount",
        "barrier",
        "basis",
        "contract_type",
        "currency",
        "duration",
        "duration_unit",
        "proposal",
        "req_id",
        "underlying_symbol",
    }
)
_DIGIT_BARRIER_CONTRACT_TYPES = frozenset({"DIGITDIFF", "DIGITMATCH", "DIGITOVER", "DIGITUNDER"})
_CALL_PUT_CONTRACT_TYPES = frozenset({"CALL", "PUT"})


def _validate_public_read_only_proposal(payload: Mapping[str, object]) -> bool:
    if set(payload) - _READ_ONLY_PROPOSAL_ALLOWED_KEYS:
        return False
    if payload.get("proposal") != 1:
        return False
    contract_type = str(payload.get("contract_type", "")).strip().upper()
    if contract_type not in READ_ONLY_PROPOSAL_CONTRACT_TYPES:
        return False
    try:
        amount = Decimal(str(payload.get("amount", "")))
    except (InvalidOperation, ValueError):
        return False
    if not amount.is_finite() or amount <= 0:
        return False
    if payload.get("basis") != "stake" or payload.get("currency") != "USD":
        return False
    if payload.get("duration_unit") != "t":
        return False
    duration = payload.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        return False
    if contract_type in _CALL_PUT_CONTRACT_TYPES and duration not in {1, 5, 10}:
        return False
    if contract_type not in _CALL_PUT_CONTRACT_TYPES and duration != 1:
        return False
    symbol = payload.get("underlying_symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        return False
    has_barrier = "barrier" in payload
    if contract_type in _DIGIT_BARRIER_CONTRACT_TYPES:
        if not has_barrier:
            return False
        try:
            barrier = int(str(payload["barrier"]))
        except (TypeError, ValueError):
            return False
        return 0 <= barrier <= 9
    return not has_barrier


def validate_deriv_ws_url(
    url: str,
    *,
    expected_demo: bool | None = None,
    expected_account_type: str | None = None,
) -> str:
    """Accept only the official public or selected OTP-authenticated endpoint."""

    if expected_account_type is not None:
        expected_account_type = expected_account_type.strip().lower()
        if expected_account_type not in {"demo", "real"} or expected_demo is not None:
            raise ValueError("expected Deriv account mode is invalid")

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
    is_real = parsed.path == REAL_WS_PATH
    if ("/real" in parsed.path and not is_real) or (is_real and expected_account_type != "real"):
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_REAL_WS_FORBIDDEN",
        )
    is_demo = parsed.path == DEMO_WS_PATH
    is_public = parsed.path == PUBLIC_WS_PATH
    if not is_demo and not is_real and not is_public:
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_WS_PATH_FORBIDDEN",
        )
    if expected_demo is not None and is_demo is not expected_demo:
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_WS_PATH_FORBIDDEN",
        )
    if expected_account_type is not None and (
        (expected_account_type == "demo" and not is_demo)
        or (expected_account_type == "real" and not is_real)
    ):
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_WS_PATH_FORBIDDEN",
        )
    if is_demo or is_real:
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


def validate_deriv_account(
    account_payload: Mapping[str, object], *, expected_account_type: str = "demo"
) -> None:
    account_type = account_payload.get("account_type")
    if expected_account_type not in {"demo", "real"}:
        raise ValueError("expected account type is invalid")
    if account_type != expected_account_type:
        raise DerivWorkerError(
            DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "DERIV_ACCOUNT_TYPE_MISMATCH",
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
    if not demo_authenticated and opcode == "proposal":
        if _validate_public_read_only_proposal(payload):
            return
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
