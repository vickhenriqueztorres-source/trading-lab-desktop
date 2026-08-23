from __future__ import annotations

from typing import Any

from apps.iqoption_worker.schema import IQOptionErrorCategory, IQOptionWorkerError
from packages.domain.models import Broker, Direction, OrderCommand

PRACTICE_BALANCE_TYPE = 4
REAL_BALANCE_TYPE = 1


def validate_iqoption_account(account_payload: dict[str, Any]) -> None:
    """Strictly validates that the IQ Option account is in Practice/Demo mode.

    Any presence of Real balance (type 1) or real account flags raises
    IQOPTION_REAL_ACCOUNT_FORBIDDEN immediately.
    """
    balance_type = account_payload.get("balance_type")
    is_demo = account_payload.get("is_demo")
    account_type = str(account_payload.get("account_type", "")).lower()

    if balance_type == REAL_BALANCE_TYPE or is_demo is False or account_type == "real":
        raise IQOptionWorkerError(
            IQOptionErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "IQOPTION_REAL_ACCOUNT_FORBIDDEN",
            "Real account execution is strictly forbidden in DualTrade Desktop",
        )

    if balance_type != PRACTICE_BALANCE_TYPE and is_demo is not True and account_type != "practice":
        raise IQOptionWorkerError(
            IQOptionErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "IQOPTION_PRACTICE_ACCOUNT_REQUIRED",
            "Account must be an authenticated Practice/Demo account",
        )


def validate_iqoption_order_command(command: OrderCommand) -> None:
    """Validates an OrderCommand before routing to IQ Option."""
    if command.broker is not Broker.IQ_OPTION:
        raise IQOptionWorkerError(
            IQOptionErrorCategory.VALIDATION_ERROR,
            "IQOPTION_BROKER_MISMATCH",
            f"Expected broker IQ_OPTION, got {command.broker.value}",
        )
    if not command.symbol or not command.symbol.strip():
        raise IQOptionWorkerError(
            IQOptionErrorCategory.VALIDATION_ERROR,
            "IQOPTION_INVALID_SYMBOL",
            "Symbol cannot be empty",
        )
    if command.direction not in (Direction.CALL, Direction.PUT):
        raise IQOptionWorkerError(
            IQOptionErrorCategory.VALIDATION_ERROR,
            "IQOPTION_INVALID_DIRECTION",
            f"Invalid order direction: {command.direction}",
        )
    if command.amount.minor_units <= 0:
        raise IQOptionWorkerError(
            IQOptionErrorCategory.VALIDATION_ERROR,
            "IQOPTION_INVALID_AMOUNT",
            "Order amount must be positive",
        )
    if not command.account_id or not command.account_id.strip():
        raise IQOptionWorkerError(
            IQOptionErrorCategory.VALIDATION_ERROR,
            "IQOPTION_INVALID_ACCOUNT_ID",
            "Account ID cannot be empty",
        )
    if command.account_id.upper().startswith("REAL"):
        raise IQOptionWorkerError(
            IQOptionErrorCategory.ACCOUNT_MODE_FORBIDDEN,
            "IQOPTION_REAL_ACCOUNT_FORBIDDEN",
            "Real account ID is strictly forbidden",
        )
