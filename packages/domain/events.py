"""Typed order lifecycle events used by the enterprise foundation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class OrderEvent:
    event_id: str
    order_id: str
    event_type: str
    timestamp: datetime
    payload: Mapping[str, Any] = field(default_factory=dict)
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.order_id.strip() or not self.event_type.strip():
            raise ValueError("event_id, order_id and event_type cannot be empty")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


def _event_type(name: str) -> type[OrderEvent]:
    def __init__(
        self: OrderEvent,
        event_id: str,
        order_id: str,
        timestamp: datetime,
        payload: Mapping[str, Any] | None = None,
        correlation_id: str = "",
    ) -> None:
        OrderEvent.__init__(
            self,
            event_id,
            order_id,
            name,
            timestamp,
            {} if payload is None else payload,
            correlation_id,
        )

    return dataclass(frozen=True, slots=True, init=False)(
        type(name, (OrderEvent,), {"__module__": __name__, "__init__": __init__})
    )


OrderCreated = _event_type("OrderCreated")
OrderAdmitted = _event_type("OrderAdmitted")
OrderReserved = _event_type("OrderReserved")
OrderSubmitting = _event_type("OrderSubmitting")
OrderAccepted = _event_type("OrderAccepted")
OrderRejectedRemote = _event_type("OrderRejectedRemote")
OrderUnknown = _event_type("OrderUnknown")
OrderReconciling = _event_type("OrderReconciling")
OrderManualReview = _event_type("OrderManualReview")

ORDER_EVENT_TYPES = (
    "OrderCreated",
    "OrderAdmitted",
    "OrderReserved",
    "OrderSubmitting",
    "OrderAccepted",
    "OrderRejectedRemote",
    "OrderUnknown",
    "OrderReconciling",
    "OrderManualReview",
)

__all__ = ["OrderEvent", "ORDER_EVENT_TYPES", *ORDER_EVENT_TYPES]
