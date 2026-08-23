from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class IQOptionOptionType(StrEnum):
    TURBO = "turbo"
    BINARY = "binary"
    DIGITAL = "digital"


class IQOptionContractStatus(StrEnum):
    OPEN = "open"
    WIN = "win"
    LOOSE = "loose"
    EQUAL = "equal"


@dataclass(frozen=True, slots=True)
class IQOptionContractRecord:
    contract_id: int
    active_id: int
    symbol: str
    direction: str
    amount: Decimal
    currency: str
    open_time: int
    close_time: int
    status: IQOptionContractStatus
    win_amount: Decimal
    client_order_id: str
    correlation_id: str
