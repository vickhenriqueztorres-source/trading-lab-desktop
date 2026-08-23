from __future__ import annotations

from enum import StrEnum
from typing import Any


class IQOptionErrorCategory(StrEnum):
    NETWORK_ERROR = "NETWORK_ERROR"
    ACCOUNT_MODE_FORBIDDEN = "ACCOUNT_MODE_FORBIDDEN"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    ORDER_REJECTED = "ORDER_REJECTED"
    RECONCILIATION_ERROR = "RECONCILIATION_ERROR"


class IQOptionWorkerError(RuntimeError):
    def __init__(
        self,
        category: IQOptionErrorCategory,
        reason_code: str,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or f"[{category.value}] {reason_code}")
        self.category = category
        self.reason_code = reason_code
        self.details = details or {}
