"""Enterprise order domain primitives for broker workers.

This module is deliberately independent from broker SDKs and from the existing
Deriv execution models.  It describes the minimum state machine used by the
IQ Option foundation and keeps monetary values as :class:`Decimal`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class OrderState(StrEnum):
    CREATED = "CREATED"
    ADMITTED = "ADMITTED"
    RESERVED = "RESERVED"
    SUBMITTING = "SUBMITTING"
    ACCEPTED = "ACCEPTED"
    REJECTED_REMOTE = "REJECTED_REMOTE"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"
    MANUAL_REVIEW = "MANUAL_REVIEW"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    normalized = value.astimezone(UTC)
    return normalized


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True, slots=True)
class Order:
    internal_order_id: str
    dedupe_key: str
    account_id: str
    strategy_id: str
    asset: str
    direction: str
    amount: Decimal
    duration: int
    state: OrderState = OrderState.CREATED
    timestamps: Mapping[str, datetime] = field(default_factory=dict)
    fencing_token: str = ""
    reconciliation_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "internal_order_id",
            "dedupe_key",
            "account_id",
            "strategy_id",
            "asset",
            "direction",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} cannot be empty")
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        if self.amount <= 0:
            raise ValueError("amount must be positive")
        if isinstance(self.duration, bool) or self.duration <= 0:
            raise ValueError("duration must be a positive integer")
        if not isinstance(self.state, OrderState):
            object.__setattr__(self, "state", OrderState(str(self.state)))
        normalized_timestamps = {str(key): _utc(value) for key, value in self.timestamps.items()}
        object.__setattr__(self, "timestamps", _mapping(normalized_timestamps))


@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: str
    dedupe_key: str
    account_id: str
    strategy_id: str
    asset: str
    direction: str
    amount: Decimal
    duration: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "intent_id",
            "dedupe_key",
            "account_id",
            "strategy_id",
            "asset",
            "direction",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} cannot be empty")
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        if self.amount <= 0:
            raise ValueError("amount must be positive")
        if (
            isinstance(self.duration, bool)
            or not isinstance(self.duration, int)
            or self.duration <= 0
        ):
            raise ValueError("duration must be a positive integer")
        object.__setattr__(self, "metadata", _mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    state: OrderState
    internal_order_id: str
    broker_order_id: str | None = None
    error_code: str | None = None
    retry_allowed: bool = False
    reconciliation_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.state, OrderState):
            object.__setattr__(self, "state", OrderState(str(self.state)))
        if not self.internal_order_id.strip():
            raise ValueError("internal_order_id cannot be empty")


__all__ = ["ExecutionResult", "Order", "OrderIntent", "OrderState"]
