from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum


class DerivErrorCategory(StrEnum):
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_REQUEST = "INVALID_REQUEST"
    SCHEMA_INCOMPATIBLE = "SCHEMA_INCOMPATIBLE"
    NETWORK_ERROR = "NETWORK_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    SUBSCRIPTION_ERROR = "SUBSCRIPTION_ERROR"
    ACCOUNT_MODE_FORBIDDEN = "ACCOUNT_MODE_FORBIDDEN"


class DerivWorkerError(RuntimeError):
    def __init__(self, category: DerivErrorCategory, reason_code: str) -> None:
        super().__init__(reason_code)
        self.category = category
        self.reason_code = reason_code


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON numeric constant: {value}")


def parse_deriv_json(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw, parse_float=Decimal, parse_constant=_reject_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_SCHEMA_INCOMPATIBLE",
        ) from exc
    if not isinstance(parsed, dict):
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_SCHEMA_INCOMPATIBLE",
        )
    return parsed


def require_mapping(payload: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_SCHEMA_INCOMPATIBLE",
        )
    return value


def require_list(payload: Mapping[str, object], name: str) -> list[object]:
    value = payload.get(name)
    if not isinstance(value, list):
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_SCHEMA_INCOMPATIBLE",
        )
    return value


def require_str(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_SCHEMA_INCOMPATIBLE",
        )
    return value


def require_int(payload: Mapping[str, object], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_SCHEMA_INCOMPATIBLE",
        )
    return value


def require_decimal(payload: Mapping[str, object], name: str) -> Decimal:
    value = payload.get(name)
    if isinstance(value, bool):
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_SCHEMA_INCOMPATIBLE",
        )
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (str, int)):
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise DerivWorkerError(
                DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                "DERIV_SCHEMA_INCOMPATIBLE",
            ) from exc
    else:
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_SCHEMA_INCOMPATIBLE",
        )
    if not result.is_finite():
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "MARKET_DATA_INVALID",
        )
    return result


def validate_response(payload: Mapping[str, object], expected_type: str) -> None:
    error = payload.get("error")
    if isinstance(error, dict):
        code = str(error.get("code", ""))
        normalized = code.lower()
        if "rate" in normalized or "throttle" in normalized:
            category = DerivErrorCategory.RATE_LIMITED
        elif "auth" in normalized or "permission" in normalized:
            category = DerivErrorCategory.AUTH_FAILED
        else:
            category = DerivErrorCategory.INVALID_REQUEST
        raise DerivWorkerError(category, f"DERIV_{category.value}")
    if payload.get("msg_type") != expected_type:
        raise DerivWorkerError(
            DerivErrorCategory.SCHEMA_INCOMPATIBLE,
            "DERIV_SCHEMA_INCOMPATIBLE",
        )


def redact_text(value: str) -> str:
    lowered = value.lower()
    if any(marker in lowered for marker in ("bearer ", "otp=", "authorization", "token")):
        return "[REDACTED]"
    return value
