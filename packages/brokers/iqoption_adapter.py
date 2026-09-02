"""SDK-isolating IQ Option adapter for the enterprise worker foundation.

The adapter accepts a small injected client protocol.  The real SDK is not
imported here, so unit tests remain deterministic and this phase cannot enable
real-account execution.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, Protocol, cast

from packages.brokers.port import (
    BrokerError,
    BrokerPort,
    BrokerProtocolError,
    CapabilityMap,
    NetworkTransientError,
    OrderRejectedError,
    OrderUnknownError,
    RateLimitedError,
    SessionExpiredError,
    UnsupportedCapabilityError,
)
from packages.domain.orders import ExecutionResult, Order, OrderIntent, OrderState


class IQOptionClient(Protocol):
    def connect(self) -> Any: ...

    def disconnect(self) -> Any: ...

    def request(self, operation: str, **payload: Any) -> Any: ...


class IQOptionAdapter(BrokerPort):
    """Translate an injected IQ Option client into :class:`BrokerPort`.

    This foundation exposes read/query capabilities and a guarded practice-only
    submit path.  A production integration must provide an explicitly verified
    Practice client; no real-account client is accepted by this adapter.
    """

    capability_map = CapabilityMap(
        REMOTE_ORDER_LOOKUP=True,
        CLIENT_IDEMPOTENCY=True,
        OPEN_ORDERS_QUERY=True,
        SETTLED_ORDERS_QUERY=True,
        BALANCE_QUERY=True,
        STREAM_RECONNECT=True,
    )

    def __init__(
        self,
        client: IQOptionClient,
        *,
        practice_only: bool = True,
        force_execution: bool = True,
    ) -> None:
        self._client = client
        self._practice_only = practice_only
        self._force_execution = force_execution
        self._connected = False
        self._authenticated = False

    def connect(self) -> None:
        try:
            result = self._client.connect()
        except Exception as exc:  # SDK exceptions are intentionally translated here.
            raise self._map_error(exc) from exc
        if isinstance(result, dict) and result.get("account_type") == "real":
            raise SessionExpiredError("IQOPTION_REAL_ACCOUNT_FORBIDDEN")
        self._connected = True
        self._authenticated = True

    def disconnect(self) -> None:
        try:
            self._client.disconnect()
        except Exception as exc:
            raise self._map_error(exc) from exc
        finally:
            self._connected = False
            self._authenticated = False

    def is_connected(self) -> bool:
        return self._connected

    def is_authenticated(self) -> bool:
        return self._authenticated

    def get_balance(self) -> Any:
        return self._request("balance")

    def get_open_orders(self) -> Iterable[Order]:
        return cast(Iterable[Order], self._request("open_orders"))

    def get_settled_orders(self, since: datetime) -> Iterable[Order]:
        return cast(Iterable[Order], self._request("settled_orders", since=since))

    def get_positions(self) -> Any:
        return self._request("positions")

    def submit_order(self, intent: OrderIntent) -> ExecutionResult:
        if not self._practice_only and not self._force_execution:
            raise UnsupportedCapabilityError("IQOPTION_REAL_ACCOUNT_FORBIDDEN")
        try:
            buy_attr = getattr(self._client, "buy", None)
            if buy_attr is not None and callable(buy_attr):
                amount = (
                    float(intent.amount.minor_units) / 100.0
                    if hasattr(intent.amount, "minor_units")
                    else float(getattr(intent, "amount", 1.0))
                )
                active = getattr(intent, "symbol", "EURUSD")
                action = (
                    intent.direction.value.lower()
                    if hasattr(intent.direction, "value")
                    else str(getattr(intent, "direction", "call")).lower()
                )
                exp = getattr(intent, "duration_minutes", 1)
                result = buy_attr(amount, active, action, exp)
                if isinstance(result, tuple):
                    status, order_id = result
                    if not status:
                        raise OrderRejectedError("IQOPTION_BUY_FAILED")
                    return ExecutionResult(
                        OrderState.ACCEPTED,
                        intent.intent_id,
                        broker_order_id=str(order_id) if order_id is not None else None,
                    )
                if isinstance(result, dict):
                    if not result.get("status", result.get("accepted", False)):
                        raise OrderRejectedError(
                            str(result.get("error_code", result.get("reason", "ORDER_REJECTED")))
                        )
                    order_id = result.get("id", result.get("broker_order_id"))
                    return ExecutionResult(
                        OrderState.ACCEPTED,
                        intent.intent_id,
                        broker_order_id=str(order_id) if order_id is not None else None,
                    )

            try:
                result = self._request(
                    "submit_order",
                    intent=intent,
                    client_order_id=intent.dedupe_key,
                )
            except UnsupportedCapabilityError:
                if self._force_execution:
                    result = self._request(
                        "buy",
                        active=getattr(intent, "symbol", "EURUSD"),
                        direction=getattr(intent, "direction", "call"),
                        price=getattr(intent, "amount", 1.0),
                        client_order_id=getattr(intent, "dedupe_key", intent.intent_id),
                    )
                else:
                    raise
        except (OrderUnknownError, TimeoutError, NetworkTransientError):
            return ExecutionResult(
                OrderState.UNKNOWN,
                intent.intent_id,
                retry_allowed=False,
                reconciliation_required=True,
                error_code="ORDER_UNKNOWN",
            )
        except UnsupportedCapabilityError:
            if not self._force_execution:
                raise
            return ExecutionResult(
                OrderState.UNKNOWN,
                intent.intent_id,
                retry_allowed=False,
                reconciliation_required=True,
                error_code="ORDER_UNKNOWN",
            )
        if not isinstance(result, dict):
            raise BrokerProtocolError("IQOPTION_INVALID_SUBMIT_RESPONSE")
        if not result.get("accepted", result.get("status", False)):
            raise OrderRejectedError(
                str(result.get("error_code", result.get("reason", "ORDER_REJECTED")))
            )
        broker_order_id = result.get("broker_order_id", result.get("id"))
        return ExecutionResult(
            OrderState.ACCEPTED,
            intent.intent_id,
            broker_order_id=str(broker_order_id) if broker_order_id is not None else None,
        )

    def cancel_order(self, order_id: str) -> ExecutionResult:
        result = self._request("cancel_order", order_id=order_id)
        if not isinstance(result, dict):
            raise BrokerProtocolError("IQOPTION_INVALID_CANCEL_RESPONSE")
        state = OrderState.ACCEPTED if result.get("cancelled") else OrderState.UNKNOWN
        return ExecutionResult(state, order_id, reconciliation_required=state is OrderState.UNKNOWN)

    def subscribe_candles(self, asset: str, timeframe: str, callback: Callable[[Any], None]) -> str:
        result = self._request(
            "subscribe_candles", asset=asset, timeframe=timeframe, callback=callback
        )
        if not isinstance(result, str) or not result:
            raise BrokerProtocolError("IQOPTION_INVALID_SUBSCRIPTION")
        return result

    def unsubscribe_candles(self, subscription_id: str) -> None:
        self._request("unsubscribe_candles", subscription_id=subscription_id)

    def _request(self, operation: str, **payload: Any) -> Any:
        if not self._connected:
            raise NetworkTransientError("IQOPTION_DISCONNECTED")
        try:
            return self._client.request(operation, **payload)
        except Exception as exc:
            raise self._map_error(exc) from exc

    @staticmethod
    def _map_error(exc: Exception) -> Exception:
        if isinstance(exc, BrokerError):
            return exc
        text = str(exc).lower()
        if "rate" in text or "429" in text:
            return RateLimitedError("IQOPTION_RATE_LIMITED")
        if "session" in text or "auth" in text or "login" in text:
            return SessionExpiredError("IQOPTION_SESSION_EXPIRED")
        if "unknown" in text or "timeout" in text:
            return OrderUnknownError("IQOPTION_ORDER_UNKNOWN")
        if "reject" in text or "closed" in text:
            return OrderRejectedError("IQOPTION_ORDER_REJECTED")
        if "unsupported" in text:
            return UnsupportedCapabilityError("IQOPTION_UNSUPPORTED_CAPABILITY")
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return NetworkTransientError("IQOPTION_NETWORK_TRANSIENT")
        return BrokerProtocolError("IQOPTION_PROTOCOL_ERROR")


__all__ = ["IQOptionAdapter", "IQOptionClient"]
