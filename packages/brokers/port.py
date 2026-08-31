"""Broker boundary for isolated workers.

No strategy or Core component should import a broker SDK.  Implementations
translate their SDK into these small, testable operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from packages.domain.orders import ExecutionResult, Order, OrderIntent


class BrokerError(RuntimeError):
    """Base error raised at the broker boundary."""


class NetworkTransientError(BrokerError):
    pass


class SessionExpiredError(BrokerError):
    pass


class RateLimitedError(BrokerError):
    pass


class OrderRejectedError(BrokerError):
    pass


class OrderUnknownError(BrokerError):
    pass


class UnsupportedCapabilityError(BrokerError):
    pass


class BrokerProtocolError(BrokerError):
    pass


class Capability(StrEnum):
    REMOTE_ORDER_LOOKUP = "REMOTE_ORDER_LOOKUP"
    CLIENT_IDEMPOTENCY = "CLIENT_IDEMPOTENCY"
    OPEN_ORDERS_QUERY = "OPEN_ORDERS_QUERY"
    SETTLED_ORDERS_QUERY = "SETTLED_ORDERS_QUERY"
    BALANCE_QUERY = "BALANCE_QUERY"
    STREAM_RECONNECT = "STREAM_RECONNECT"


@dataclass(frozen=True, slots=True)
class CapabilityMap:
    REMOTE_ORDER_LOOKUP: bool = False
    CLIENT_IDEMPOTENCY: bool = False
    OPEN_ORDERS_QUERY: bool = False
    SETTLED_ORDERS_QUERY: bool = False
    BALANCE_QUERY: bool = False
    STREAM_RECONNECT: bool = False

    def supports(self, capability: Capability | str) -> bool:
        name = capability.value if isinstance(capability, Capability) else str(capability)
        return bool(getattr(self, name, False))


class BrokerPort(ABC):
    """Stable port implemented by a broker-specific worker adapter."""

    capability_map: CapabilityMap

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def is_authenticated(self) -> bool: ...

    @abstractmethod
    def get_balance(self) -> Any: ...

    @abstractmethod
    def get_open_orders(self) -> Iterable[Order]: ...

    @abstractmethod
    def get_settled_orders(self, since: datetime) -> Iterable[Order]: ...

    @abstractmethod
    def get_positions(self) -> Any: ...

    @abstractmethod
    def submit_order(self, intent: OrderIntent) -> ExecutionResult: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> ExecutionResult: ...

    @abstractmethod
    def subscribe_candles(
        self, asset: str, timeframe: str, callback: Callable[[Any], None]
    ) -> str: ...

    @abstractmethod
    def unsubscribe_candles(self, subscription_id: str) -> None: ...


__all__ = [
    "BrokerError",
    "BrokerPort",
    "BrokerProtocolError",
    "Capability",
    "CapabilityMap",
    "NetworkTransientError",
    "OrderRejectedError",
    "OrderUnknownError",
    "RateLimitedError",
    "SessionExpiredError",
    "UnsupportedCapabilityError",
]
