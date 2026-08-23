from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from apps.deriv_worker.schema import DerivErrorCategory, DerivWorkerError
from apps.deriv_worker.validators import (
    validate_outbound_deriv_request,
)


class DerivOperation(StrEnum):
    PING = "ping"
    TIME = "time"
    ACTIVE_SYMBOLS = "active_symbols"
    CONTRACTS_LIST = "contracts_list"
    CONTRACTS_FOR = "contracts_for"
    TICKS = "ticks"
    TICKS_HISTORY = "ticks_history"
    FORGET = "forget"
    FORGET_ALL = "forget_all"
    BALANCE = "balance"
    BUY = "buy"
    PROPOSAL = "proposal"
    PROPOSAL_OPEN_CONTRACT = "proposal_open_contract"
    STATEMENT = "statement"
    PROFIT_TABLE = "profit_table"


PUBLIC_READ_ONLY_OPERATIONS = frozenset(
    {
        DerivOperation.PING,
        DerivOperation.TIME,
        DerivOperation.ACTIVE_SYMBOLS,
        DerivOperation.CONTRACTS_LIST,
        DerivOperation.CONTRACTS_FOR,
        DerivOperation.TICKS,
        DerivOperation.TICKS_HISTORY,
        DerivOperation.FORGET,
        DerivOperation.FORGET_ALL,
    }
)

DEMO_OPERATIONS = PUBLIC_READ_ONLY_OPERATIONS | {
    DerivOperation.BALANCE,
    DerivOperation.BUY,
    DerivOperation.PROPOSAL,
    DerivOperation.PROPOSAL_OPEN_CONTRACT,
    DerivOperation.STATEMENT,
    DerivOperation.PROFIT_TABLE,
}

DEMO_READ_ONLY_OPERATIONS = PUBLIC_READ_ONLY_OPERATIONS | {DerivOperation.BALANCE}


def validate_read_only_request(
    operation: DerivOperation | str,
    payload: Mapping[str, object],
    *,
    demo_authenticated: bool,
) -> DerivOperation:
    name = operation.value if isinstance(operation, DerivOperation) else operation
    validate_outbound_deriv_request(name, payload, demo_authenticated=demo_authenticated)
    try:
        normalized = DerivOperation(name)
    except ValueError as exc:
        raise DerivWorkerError(
            DerivErrorCategory.INVALID_REQUEST,
            "DERIV_OPERATION_NOT_ALLOWLISTED",
        ) from exc
    allowed = DEMO_OPERATIONS if demo_authenticated else PUBLIC_READ_ONLY_OPERATIONS
    if normalized not in allowed or normalized.value not in payload:
        raise DerivWorkerError(
            DerivErrorCategory.INVALID_REQUEST,
            "DERIV_OPERATION_NOT_ALLOWLISTED",
        )
    return normalized
