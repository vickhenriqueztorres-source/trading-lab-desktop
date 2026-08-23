from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from packages.domain.market import (
    BrokerAccountBalance,
    BrokerCapabilities,
    BrokerClockSnapshot,
    ContractMetadata,
    MarketCandle,
    MarketSymbol,
    MarketTick,
)
from packages.domain.models import (
    BrokerOrderEvent,
    OrderCommand,
    OrderStatusQuery,
    ReconciliationEvidence,
    StatusQueryOutcome,
    WorkerOutcome,
)
from packages.protocol.envelope import Envelope, MessageType
from packages.protocol.errors import ProtocolError, ProtocolErrorCode


def _require(payload: Mapping[str, object], name: str, expected: type[object]) -> object:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, expected):
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            f"invalid message payload field: {name}",
        )
    return value


@dataclass(frozen=True, slots=True)
class WorkerCapabilities:
    broker: str
    account_modes: tuple[str, ...]
    products: tuple[str, ...]
    supports_reconciliation: bool
    supports_quotes: bool
    supports_order_status_query: bool
    worker_version: str
    supports_order_events: bool = False
    can_submit_orders: bool = True
    supports_market_data: bool = False
    connection_mode: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "broker": self.broker,
            "account_modes": self.account_modes,
            "products": self.products,
            "supports_reconciliation": self.supports_reconciliation,
            "supports_quotes": self.supports_quotes,
            "supports_order_status_query": self.supports_order_status_query,
            "supports_order_events": self.supports_order_events,
            "worker_version": self.worker_version,
            "can_submit_orders": self.can_submit_orders,
            "supports_market_data": self.supports_market_data,
            "connection_mode": self.connection_mode,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> WorkerCapabilities:
        broker = _require(payload, "broker", str)
        modes = payload.get("account_modes")
        products = payload.get("products")
        reconciliation = payload.get("supports_reconciliation")
        quotes = payload.get("supports_quotes")
        order_status_query = payload.get("supports_order_status_query")
        order_events = payload.get("supports_order_events", False)
        worker_version = _require(payload, "worker_version", str)
        can_submit_orders = payload.get("can_submit_orders", True)
        supports_market_data = payload.get("supports_market_data", False)
        connection_mode = payload.get("connection_mode")
        if (
            not isinstance(modes, list)
            or not isinstance(products, list)
            or not isinstance(reconciliation, bool)
            or not isinstance(quotes, bool)
            or not isinstance(order_status_query, bool)
            or not isinstance(order_events, bool)
            or not isinstance(can_submit_orders, bool)
            or not isinstance(supports_market_data, bool)
            or (connection_mode is not None and not isinstance(connection_mode, str))
        ):
            raise ProtocolError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                "capability lists and flags have invalid types",
            )
        if not all(isinstance(item, str) and item for item in modes + products):
            raise ProtocolError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                "capability lists must contain non-empty strings",
            )
        return cls(
            broker=str(broker),
            account_modes=tuple(str(item) for item in modes),
            products=tuple(str(item) for item in products),
            supports_reconciliation=reconciliation,
            supports_quotes=quotes,
            supports_order_status_query=order_status_query,
            worker_version=str(worker_version),
            supports_order_events=order_events,
            can_submit_orders=can_submit_orders,
            supports_market_data=supports_market_data,
            connection_mode=connection_mode,
        )


@dataclass(frozen=True, slots=True)
class WorkerSubmissionResult:
    outcome: WorkerOutcome
    broker_order_id: str | None
    response_message_id: str
    correlation_id: str
    causation_id: str
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class OrderStatusResult:
    outcome: StatusQueryOutcome
    evidence: ReconciliationEvidence | None
    response_message_id: str
    correlation_id: str
    causation_id: str
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.outcome is StatusQueryOutcome.FOUND and self.evidence is None:
            raise ValueError("FOUND status result requires evidence")
        if self.outcome is not StatusQueryOutcome.FOUND and self.evidence is not None:
            raise ValueError("only FOUND status result may contain evidence")


def parse_order_submit(envelope: Envelope) -> OrderCommand:
    if envelope.message_type is not MessageType.ORDER_SUBMIT:
        raise ProtocolError(
            ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
            "expected ORDER_SUBMIT",
        )
    payload = dict(envelope.payload)
    try:
        command = OrderCommand.from_payload(payload)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "invalid ORDER_SUBMIT payload",
        ) from exc
    if command.message_id != envelope.message_id:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "ORDER_SUBMIT message_id does not match envelope",
        )
    if command.correlation_id != envelope.correlation_id:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "ORDER_SUBMIT correlation_id does not match envelope",
        )
    if command.deadline_at != envelope.deadline_at:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "ORDER_SUBMIT deadline does not match envelope",
        )
    return command


def parse_order_response(envelope: Envelope) -> WorkerSubmissionResult:
    outcomes = {
        MessageType.ORDER_ACCEPTED: WorkerOutcome.ACCEPTED,
        MessageType.ORDER_REJECTED: WorkerOutcome.REJECTED,
        MessageType.ORDER_STATUS_UNKNOWN: WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND,
    }
    outcome = outcomes.get(envelope.message_type)
    if outcome is None:
        raise ProtocolError(
            ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
            "message is not an order response",
        )
    order_id = _require(envelope.payload, "order_id", str)
    broker_order_id = envelope.payload.get("broker_order_id")
    reason_code = envelope.payload.get("reason_code")
    if broker_order_id is not None and not isinstance(broker_order_id, str):
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "broker_order_id must be a string or null",
        )
    if reason_code is not None and not isinstance(reason_code, str):
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "reason_code must be a string or null",
        )
    if not order_id:
        raise ProtocolError(ProtocolErrorCode.IPC_INVALID_ENVELOPE, "order_id is required")
    if envelope.causation_id is None:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "order response requires causation_id",
        )
    return WorkerSubmissionResult(
        outcome=outcome,
        broker_order_id=broker_order_id,
        response_message_id=envelope.message_id,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        reason_code=reason_code,
    )


def parse_order_status_request(envelope: Envelope) -> OrderStatusQuery:
    if envelope.message_type is not MessageType.ORDER_STATUS_REQUEST:
        raise ProtocolError(
            ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
            "expected ORDER_STATUS_REQUEST",
        )
    try:
        return OrderStatusQuery.from_payload(dict(envelope.payload), envelope.correlation_id)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "invalid ORDER_STATUS_REQUEST payload",
        ) from exc


def parse_order_status_response(envelope: Envelope) -> OrderStatusResult:
    if envelope.message_type is not MessageType.ORDER_STATUS_RESPONSE:
        raise ProtocolError(
            ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
            "expected ORDER_STATUS_RESPONSE",
        )
    outcome_value = _require(envelope.payload, "query_outcome", str)
    reason_code = envelope.payload.get("reason_code")
    raw_evidence = envelope.payload.get("evidence")
    if reason_code is not None and not isinstance(reason_code, str):
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "status reason_code must be a string or null",
        )
    try:
        outcome = StatusQueryOutcome(str(outcome_value))
    except ValueError as exc:
        raise ProtocolError(
            ProtocolErrorCode.RECONCILIATION_INVALID_RESPONSE,
            "unknown status query outcome",
        ) from exc
    evidence: ReconciliationEvidence | None = None
    if raw_evidence is not None:
        if not isinstance(raw_evidence, dict):
            raise ProtocolError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                "status evidence must be an object or null",
            )
        try:
            evidence = ReconciliationEvidence.from_payload(raw_evidence)
        except (TypeError, ValueError) as exc:
            raise ProtocolError(
                ProtocolErrorCode.RECONCILIATION_INVALID_RESPONSE,
                "invalid reconciliation evidence",
            ) from exc
    if envelope.causation_id is None:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "status response requires causation_id",
        )
    try:
        return OrderStatusResult(
            outcome=outcome,
            evidence=evidence,
            response_message_id=envelope.message_id,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            reason_code=reason_code,
        )
    except ValueError as exc:
        raise ProtocolError(
            ProtocolErrorCode.RECONCILIATION_INVALID_RESPONSE,
            "status response evidence does not match outcome",
        ) from exc


def parse_order_event(envelope: Envelope) -> BrokerOrderEvent:
    if envelope.message_type is not MessageType.ORDER_EVENT:
        raise ProtocolError(
            ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
            "expected ORDER_EVENT",
        )
    if envelope.causation_id is not None:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "unsolicited ORDER_EVENT cannot have causation_id",
        )
    try:
        event = BrokerOrderEvent.from_payload(dict(envelope.payload))
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "invalid ORDER_EVENT payload",
        ) from exc
    if event.correlation_id != envelope.correlation_id:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "ORDER_EVENT correlation_id does not match envelope",
        )
    return event


def parse_broker_capabilities_response(envelope: Envelope) -> BrokerCapabilities:
    if envelope.message_type is not MessageType.BROKER_CAPABILITIES_RESPONSE:
        raise ProtocolError(ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE, "expected capabilities")
    try:
        return BrokerCapabilities.from_payload(envelope.payload)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            "invalid broker capabilities payload",
        ) from exc


def parse_market_symbols_response(envelope: Envelope) -> tuple[MarketSymbol, ...]:
    if envelope.message_type is not MessageType.MARKET_SYMBOLS_RESPONSE:
        raise ProtocolError(ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE, "expected symbols")
    raw = envelope.payload.get("symbols")
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ProtocolError(ProtocolErrorCode.IPC_INVALID_ENVELOPE, "invalid symbols payload")
    try:
        return tuple(MarketSymbol.from_payload(item) for item in raw)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE, "invalid market symbol"
        ) from exc


def parse_market_contracts_response(envelope: Envelope) -> tuple[ContractMetadata, ...]:
    if envelope.message_type is not MessageType.MARKET_CONTRACTS_RESPONSE:
        raise ProtocolError(ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE, "expected contracts")
    raw = envelope.payload.get("contracts")
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ProtocolError(ProtocolErrorCode.IPC_INVALID_ENVELOPE, "invalid contracts payload")
    try:
        return tuple(ContractMetadata.from_payload(item) for item in raw)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE, "invalid contract metadata"
        ) from exc


def parse_market_tick(envelope: Envelope) -> MarketTick:
    if envelope.message_type not in {
        MessageType.MARKET_TICK_SUBSCRIBED,
        MessageType.MARKET_TICK_EVENT,
    }:
        raise ProtocolError(ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE, "expected market tick")
    raw = envelope.payload.get("tick")
    if not isinstance(raw, dict):
        raise ProtocolError(ProtocolErrorCode.IPC_INVALID_ENVELOPE, "invalid market tick payload")
    try:
        return MarketTick.from_payload(raw)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(ProtocolErrorCode.IPC_INVALID_ENVELOPE, "invalid market tick") from exc


def parse_market_history_response(
    envelope: Envelope,
) -> tuple[tuple[MarketTick, ...], tuple[MarketCandle, ...]]:
    if envelope.message_type is not MessageType.MARKET_HISTORY_RESPONSE:
        raise ProtocolError(ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE, "expected market history")
    ticks = envelope.payload.get("ticks")
    candles = envelope.payload.get("candles")
    if (
        not isinstance(ticks, list)
        or not isinstance(candles, list)
        or not all(isinstance(item, dict) for item in ticks + candles)
    ):
        raise ProtocolError(ProtocolErrorCode.IPC_INVALID_ENVELOPE, "invalid history payload")
    try:
        return (
            tuple(MarketTick.from_payload(item) for item in ticks),
            tuple(MarketCandle.from_payload(item) for item in candles),
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolError(ProtocolErrorCode.IPC_INVALID_ENVELOPE, "invalid history item") from exc


def parse_broker_clock_response(envelope: Envelope) -> BrokerClockSnapshot:
    if envelope.message_type is not MessageType.BROKER_CLOCK_RESPONSE:
        raise ProtocolError(ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE, "expected broker clock")
    try:
        return BrokerClockSnapshot.from_payload(envelope.payload)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(ProtocolErrorCode.IPC_INVALID_ENVELOPE, "invalid broker clock") from exc


def parse_broker_balance_response(envelope: Envelope) -> BrokerAccountBalance:
    if envelope.message_type is not MessageType.BROKER_BALANCE_RESPONSE:
        raise ProtocolError(ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE, "expected broker balance")
    try:
        return BrokerAccountBalance.from_payload(envelope.payload)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE, "invalid broker balance"
        ) from exc
