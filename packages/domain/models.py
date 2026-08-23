from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Broker(StrEnum):
    DERIV = "DERIV"
    IQ_OPTION = "IQ_OPTION"


class Direction(StrEnum):
    CALL = "CALL"
    PUT = "PUT"


class TradeIntentState(StrEnum):
    CREATED = "CREATED"


class RiskReservationState(StrEnum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"


class OutboxState(StrEnum):
    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    DISPATCHED = "DISPATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    CANCELLED = "CANCELLED"
    BLOCKED_NOT_SENT = "BLOCKED_NOT_SENT"
    RECONCILED = "RECONCILED"


class OrderState(StrEnum):
    OUTBOXED = "OUTBOXED"
    DISPATCHING = "DISPATCHING"
    ACCEPTED = "ACCEPTED"
    OPEN = "OPEN"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"
    SETTLED = "SETTLED"
    SETTLEMENT_UNKNOWN = "SETTLEMENT_UNKNOWN"
    REJECTED = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    SEND_BLOCKED = "SEND_BLOCKED"

    @property
    def is_terminal(self) -> bool:
        return self in {self.SETTLED, self.REJECTED}


class WorkerOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    TIMEOUT_AFTER_POSSIBLE_SEND = "TIMEOUT_AFTER_POSSIBLE_SEND"


class ExternalOrderStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    OPEN = "OPEN"
    SETTLED = "SETTLED"
    EXTERNAL_UNKNOWN = "EXTERNAL_UNKNOWN"
    SETTLEMENT_UNKNOWN = "SETTLEMENT_UNKNOWN"


class StatusQueryOutcome(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class ReconciliationSource(StrEnum):
    STATUS_QUERY = "STATUS_QUERY"


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_aware_utc(value: datetime, field: str) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError(f"{field} must be timezone-aware")
    if offset.total_seconds() != 0:
        raise ValueError(f"{field} must be UTC")


@dataclass(frozen=True, slots=True)
class Money:
    minor_units: int
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.minor_units, bool) or not isinstance(self.minor_units, int):
            raise TypeError("minor_units must be an integer")
        normalized_currency = self.currency.strip().upper()
        if len(normalized_currency) != 3 or not normalized_currency.isalpha():
            raise ValueError("currency must be a three-letter code")
        object.__setattr__(self, "currency", normalized_currency)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    correlation_id: str
    broker: Broker
    account_id: str
    product: str
    symbol: str
    direction: Direction
    amount: Money
    strategy_id: str
    strategy_version: str
    deadline_at: datetime

    def __post_init__(self) -> None:
        require_aware_utc(self.deadline_at, "deadline_at")
        for field_name in (
            "correlation_id",
            "account_id",
            "product",
            "symbol",
            "strategy_id",
            "strategy_version",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} cannot be empty")
        if self.amount.minor_units <= 0:
            raise ValueError("order amount must be positive")


@dataclass(frozen=True, slots=True)
class OrderCommand:
    message_id: str
    correlation_id: str
    intent_id: str
    order_id: str
    broker: Broker
    account_id: str
    product: str
    symbol: str
    direction: Direction
    amount: Money
    deadline_at: datetime

    def __post_init__(self) -> None:
        require_aware_utc(self.deadline_at, "deadline_at")
        for field_name in (
            "message_id",
            "correlation_id",
            "intent_id",
            "order_id",
            "account_id",
            "product",
            "symbol",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} cannot be empty")
        if self.amount.minor_units <= 0:
            raise ValueError("order amount must be positive")

    def to_payload(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "correlation_id": self.correlation_id,
            "intent_id": self.intent_id,
            "order_id": self.order_id,
            "broker": self.broker.value,
            "account_id": self.account_id,
            "product": self.product,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "amount_minor": self.amount.minor_units,
            "currency": self.amount.currency,
            "deadline_at": self.deadline_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> OrderCommand:
        required = {
            "message_id": str,
            "correlation_id": str,
            "intent_id": str,
            "order_id": str,
            "broker": str,
            "account_id": str,
            "product": str,
            "symbol": str,
            "direction": str,
            "amount_minor": int,
            "currency": str,
            "deadline_at": str,
        }
        for name, expected_type in required.items():
            value = payload.get(name)
            if isinstance(value, bool) or not isinstance(value, expected_type):
                raise ValueError(f"invalid external payload field: {name}")
        deadline_at = datetime.fromisoformat(payload["deadline_at"])
        require_aware_utc(deadline_at, "deadline_at")
        return cls(
            message_id=payload["message_id"],
            correlation_id=payload["correlation_id"],
            intent_id=payload["intent_id"],
            order_id=payload["order_id"],
            broker=Broker(payload["broker"]),
            account_id=payload["account_id"],
            product=payload["product"],
            symbol=payload["symbol"],
            direction=Direction(payload["direction"]),
            amount=Money(payload["amount_minor"], payload["currency"]),
            deadline_at=deadline_at,
        )


@dataclass(frozen=True, slots=True)
class BrokerEvent:
    event_id: str
    intent_id: str
    new_state: OrderState
    occurred_at: datetime
    broker_order_id: str | None = None
    realized_pnl_minor: int | None = None

    def __post_init__(self) -> None:
        require_aware_utc(self.occurred_at, "occurred_at")
        if not self.event_id or not self.intent_id:
            raise ValueError("event_id and intent_id are required")
        if isinstance(self.realized_pnl_minor, bool):
            raise TypeError("realized_pnl_minor must use integer minor units")
        if self.realized_pnl_minor is not None and self.new_state is not OrderState.SETTLED:
            raise ValueError("realized P&L is only valid for SETTLED events")


@dataclass(frozen=True, slots=True)
class BrokerOrderEvent:
    event_id: str
    event_version: int
    broker: Broker
    account_id: str
    client_order_ref: str
    broker_order_id: str
    correlation_id: str
    external_sequence: int | None
    external_status: ExternalOrderStatus
    occurred_at: datetime
    observed_at: datetime
    product: str
    symbol: str
    direction: Direction
    amount: Money
    result_minor: int | None
    result_currency: str | None
    evidence_hash: str

    def __post_init__(self) -> None:
        require_aware_utc(self.occurred_at, "occurred_at")
        require_aware_utc(self.observed_at, "observed_at")
        for field_name in (
            "event_id",
            "account_id",
            "client_order_ref",
            "broker_order_id",
            "correlation_id",
            "product",
            "symbol",
            "evidence_hash",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} cannot be empty")
        if (
            isinstance(self.event_version, bool)
            or not isinstance(self.event_version, int)
            or self.event_version <= 0
        ):
            raise ValueError("event_version must be a positive integer")
        if self.external_sequence is not None and (
            isinstance(self.external_sequence, bool)
            or not isinstance(self.external_sequence, int)
            or self.external_sequence <= 0
        ):
            raise ValueError("external_sequence must be a positive integer or null")
        if self.amount.minor_units <= 0:
            raise ValueError("event amount must be positive")
        if isinstance(self.result_minor, bool):
            raise TypeError("result_minor must use integer minor units")
        if self.external_status is ExternalOrderStatus.SETTLED:
            if self.result_minor is None or self.result_currency is None:
                raise ValueError("SETTLED event requires result amount and currency")
        elif self.result_minor is not None or self.result_currency is not None:
            raise ValueError("result is only valid for SETTLED event")
        if self.result_currency is not None:
            normalized = self.result_currency.strip().upper()
            if len(normalized) != 3 or not normalized.isalpha():
                raise ValueError("result_currency must be a three-letter code")
            object.__setattr__(self, "result_currency", normalized)
        if self.external_status is ExternalOrderStatus.EXTERNAL_UNKNOWN:
            raise ValueError("EXTERNAL_UNKNOWN is not a lifecycle event")
        if self.evidence_hash != self.expected_evidence_hash():
            raise ValueError("broker event evidence_hash does not match canonical payload")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_version": self.event_version,
            "broker": self.broker.value,
            "account_id": self.account_id,
            "client_order_ref": self.client_order_ref,
            "broker_order_id": self.broker_order_id,
            "correlation_id": self.correlation_id,
            "external_sequence": self.external_sequence,
            "external_status": self.external_status.value,
            "occurred_at": self.occurred_at.isoformat(),
            "observed_at": self.observed_at.isoformat(),
            "product": self.product,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "amount_minor": self.amount.minor_units,
            "currency": self.amount.currency,
            "result_minor": self.result_minor,
            "result_currency": self.result_currency,
        }

    def expected_evidence_hash(self) -> str:
        return self.evidence_hash_for_payload(self.canonical_payload())

    @staticmethod
    def evidence_hash_for_payload(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        return {**self.canonical_payload(), "evidence_hash": self.evidence_hash}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> BrokerOrderEvent:
        required = {
            "event_id": str,
            "event_version": int,
            "broker": str,
            "account_id": str,
            "client_order_ref": str,
            "broker_order_id": str,
            "correlation_id": str,
            "external_status": str,
            "occurred_at": str,
            "observed_at": str,
            "product": str,
            "symbol": str,
            "direction": str,
            "amount_minor": int,
            "currency": str,
            "evidence_hash": str,
        }
        for name, expected_type in required.items():
            value = payload.get(name)
            if isinstance(value, bool) or not isinstance(value, expected_type):
                raise ValueError(f"invalid broker event field: {name}")
        external_sequence = payload.get("external_sequence")
        result_minor = payload.get("result_minor")
        result_currency = payload.get("result_currency")
        if external_sequence is not None and (
            isinstance(external_sequence, bool) or not isinstance(external_sequence, int)
        ):
            raise ValueError("invalid broker event field: external_sequence")
        if result_minor is not None and (
            isinstance(result_minor, bool) or not isinstance(result_minor, int)
        ):
            raise ValueError("invalid broker event field: result_minor")
        if result_currency is not None and not isinstance(result_currency, str):
            raise ValueError("invalid broker event field: result_currency")
        return cls(
            event_id=payload["event_id"],
            event_version=payload["event_version"],
            broker=Broker(payload["broker"]),
            account_id=payload["account_id"],
            client_order_ref=payload["client_order_ref"],
            broker_order_id=payload["broker_order_id"],
            correlation_id=payload["correlation_id"],
            external_sequence=external_sequence,
            external_status=ExternalOrderStatus(payload["external_status"]),
            occurred_at=datetime.fromisoformat(payload["occurred_at"]),
            observed_at=datetime.fromisoformat(payload["observed_at"]),
            product=payload["product"],
            symbol=payload["symbol"],
            direction=Direction(payload["direction"]),
            amount=Money(payload["amount_minor"], payload["currency"]),
            result_minor=result_minor,
            result_currency=result_currency,
            evidence_hash=payload["evidence_hash"],
        )


@dataclass(frozen=True, slots=True)
class OrderStatusQuery:
    correlation_id: str
    intent_id: str
    order_id: str
    client_order_ref: str
    broker: Broker
    account_id: str
    product: str
    symbol: str
    direction: Direction
    amount: Money
    broker_order_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "correlation_id",
            "intent_id",
            "order_id",
            "client_order_ref",
            "account_id",
            "product",
            "symbol",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} cannot be empty")
        if self.broker_order_id is not None and not self.broker_order_id.strip():
            raise ValueError("broker_order_id cannot be blank")
        if self.amount.minor_units <= 0:
            raise ValueError("order amount must be positive")

    def to_payload(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "order_id": self.order_id,
            "client_order_ref": self.client_order_ref,
            "broker": self.broker.value,
            "account_id": self.account_id,
            "product": self.product,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "amount_minor": self.amount.minor_units,
            "currency": self.amount.currency,
            "broker_order_id": self.broker_order_id,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any], correlation_id: str) -> OrderStatusQuery:
        required = {
            "intent_id": str,
            "order_id": str,
            "client_order_ref": str,
            "broker": str,
            "account_id": str,
            "product": str,
            "symbol": str,
            "direction": str,
            "amount_minor": int,
            "currency": str,
        }
        for name, expected_type in required.items():
            value = payload.get(name)
            if isinstance(value, bool) or not isinstance(value, expected_type):
                raise ValueError(f"invalid status query field: {name}")
        broker_order_id = payload.get("broker_order_id")
        if broker_order_id is not None and not isinstance(broker_order_id, str):
            raise ValueError("invalid status query field: broker_order_id")
        return cls(
            correlation_id=correlation_id,
            intent_id=payload["intent_id"],
            order_id=payload["order_id"],
            client_order_ref=payload["client_order_ref"],
            broker=Broker(payload["broker"]),
            account_id=payload["account_id"],
            product=payload["product"],
            symbol=payload["symbol"],
            direction=Direction(payload["direction"]),
            amount=Money(payload["amount_minor"], payload["currency"]),
            broker_order_id=broker_order_id,
        )


@dataclass(frozen=True, slots=True)
class ReconciliationEvidence:
    evidence_id: str
    source: ReconciliationSource
    observed_at: datetime
    client_order_ref: str
    broker_order_id: str | None
    external_status: ExternalOrderStatus
    broker: Broker
    account_id: str
    product: str
    symbol: str
    direction: Direction
    amount: Money
    evidence_version: int
    realized_pnl_minor: int | None = None
    raw_reference_hash: str | None = None

    def __post_init__(self) -> None:
        require_aware_utc(self.observed_at, "observed_at")
        for field_name in (
            "evidence_id",
            "client_order_ref",
            "account_id",
            "product",
            "symbol",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} cannot be empty")
        if self.broker_order_id is not None and not self.broker_order_id.strip():
            raise ValueError("broker_order_id cannot be blank")
        if (
            isinstance(self.evidence_version, bool)
            or not isinstance(self.evidence_version, int)
            or self.evidence_version <= 0
        ):
            raise ValueError("evidence_version must be a positive integer")
        if isinstance(self.realized_pnl_minor, bool):
            raise TypeError("realized_pnl_minor must use integer minor units")
        if self.external_status is ExternalOrderStatus.SETTLED:
            if self.realized_pnl_minor is None:
                raise ValueError("SETTLED evidence requires realized P&L")
        elif self.realized_pnl_minor is not None:
            raise ValueError("realized P&L is only valid for SETTLED evidence")
        if self.raw_reference_hash is not None and not self.raw_reference_hash.strip():
            raise ValueError("raw_reference_hash cannot be blank")

    def to_payload(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source.value,
            "observed_at": self.observed_at.isoformat(),
            "client_order_ref": self.client_order_ref,
            "broker_order_id": self.broker_order_id,
            "external_status": self.external_status.value,
            "broker": self.broker.value,
            "account_id": self.account_id,
            "product": self.product,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "amount_minor": self.amount.minor_units,
            "currency": self.amount.currency,
            "evidence_version": self.evidence_version,
            "realized_pnl_minor": self.realized_pnl_minor,
            "raw_reference_hash": self.raw_reference_hash,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ReconciliationEvidence:
        required = {
            "evidence_id": str,
            "source": str,
            "observed_at": str,
            "client_order_ref": str,
            "external_status": str,
            "broker": str,
            "account_id": str,
            "product": str,
            "symbol": str,
            "direction": str,
            "amount_minor": int,
            "currency": str,
            "evidence_version": int,
        }
        for name, expected_type in required.items():
            value = payload.get(name)
            if isinstance(value, bool) or not isinstance(value, expected_type):
                raise ValueError(f"invalid reconciliation evidence field: {name}")
        broker_order_id = payload.get("broker_order_id")
        realized_pnl_minor = payload.get("realized_pnl_minor")
        raw_reference_hash = payload.get("raw_reference_hash")
        if broker_order_id is not None and not isinstance(broker_order_id, str):
            raise ValueError("invalid reconciliation evidence field: broker_order_id")
        if realized_pnl_minor is not None and (
            isinstance(realized_pnl_minor, bool) or not isinstance(realized_pnl_minor, int)
        ):
            raise ValueError("invalid reconciliation evidence field: realized_pnl_minor")
        if raw_reference_hash is not None and not isinstance(raw_reference_hash, str):
            raise ValueError("invalid reconciliation evidence field: raw_reference_hash")
        observed_at = datetime.fromisoformat(payload["observed_at"])
        return cls(
            evidence_id=payload["evidence_id"],
            source=ReconciliationSource(payload["source"]),
            observed_at=observed_at,
            client_order_ref=payload["client_order_ref"],
            broker_order_id=broker_order_id,
            external_status=ExternalOrderStatus(payload["external_status"]),
            broker=Broker(payload["broker"]),
            account_id=payload["account_id"],
            product=payload["product"],
            symbol=payload["symbol"],
            direction=Direction(payload["direction"]),
            amount=Money(payload["amount_minor"], payload["currency"]),
            evidence_version=payload["evidence_version"],
            realized_pnl_minor=realized_pnl_minor,
            raw_reference_hash=raw_reference_hash,
        )
