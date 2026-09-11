from __future__ import annotations

import queue
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from apps.iqoption_worker.schema import IQOptionErrorCategory, IQOptionWorkerError
from packages.brokers.iqoption.community_read_only import IQOptionExternalError
from packages.brokers.iqoption.result_parser import (
    IQOPTION_EVENT_NAME_KEY,
    IQOptionResultError,
    IQOptionResultSource,
    parse_iqoption_financial_result,
)
from packages.brokers.iqoption.validators import validate_iqoption_order_command
from packages.domain.models import (
    Broker,
    BrokerOrderEvent,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderCommand,
    WorkerOutcome,
)
from packages.protocol.messages import WorkerSubmissionResult


@dataclass(slots=True)
class TrackedIQOptionOrder:
    order_id: str
    correlation_id: str
    client_order_ref: str
    broker_order_id: str | None
    symbol: str
    direction: Direction
    amount: Money
    product: str
    account_id: str
    created_at_utc: datetime
    last_status: ExternalOrderStatus
    _sequence: int = 0

    def next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence


class IQOptionOrderTransport(Protocol):
    def request(
        self,
        name: str,
        msg: Mapping[str, Any],
        *,
        timeout: float = 2.0,
    ) -> dict[str, Any]: ...

    def receive_contract(self, *, timeout: float = 0.1) -> dict[str, Any] | None: ...


class IQOptionOrderSession:
    """Manages order submission and contract event streaming for IQ Option practice."""

    def __init__(
        self,
        transport: IQOptionOrderTransport,
        *,
        account_id: str = "PRACTICE_ACCOUNT",
        practice_mode: bool = True,
    ) -> None:
        if not practice_mode:
            raise IQOptionWorkerError(
                IQOptionErrorCategory.ACCOUNT_MODE_FORBIDDEN,
                "IQOPTION_REAL_ACCOUNT_FORBIDDEN",
                "Real account mode is forbidden",
            )
        self._transport = transport
        self._account_id = account_id
        self.practice_mode = practice_mode
        self._lock = threading.Lock()
        self._tracked: dict[str, TrackedIQOptionOrder] = {}  # keyed by str(broker_order_id)
        self._tracked_by_ref: dict[str, TrackedIQOptionOrder] = {}  # keyed by order_id
        self._events: queue.Queue[BrokerOrderEvent] = queue.Queue(maxsize=1024)
        self._contract_events_overflow_total = 0
        self._reconciliation_required = False

    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult:
        transport_entered = False
        try:
            validate_iqoption_order_command(command)
            if command.deadline_at <= datetime.now(UTC):
                raise IQOptionExternalError(
                    "IQOPTION_ENTRY_WINDOW_MISSED",
                    submission_not_sent=True,
                )
            stake_decimal = Decimal(command.amount.minor_units) / Decimal(100)
            stake_str = f"{stake_decimal:.2f}"
            payload = {
                "active": command.symbol,
                "direction": command.direction.value.lower(),
                "price": stake_str,
                "client_order_id": command.order_id,
                "correlation_id": command.correlation_id,
                "duration": command.duration,
            }
            if command.contract_expiry_at is not None:
                payload["expiry_epoch"] = int(command.contract_expiry_at.timestamp())
            tracked = TrackedIQOptionOrder(
                order_id=command.order_id,
                correlation_id=command.correlation_id,
                client_order_ref=command.order_id,
                broker_order_id=None,
                symbol=command.symbol,
                direction=command.direction,
                amount=command.amount,
                product=command.product,
                account_id=command.account_id,
                created_at_utc=datetime.now(UTC),
                last_status=ExternalOrderStatus.ACCEPTED,
            )
            with self._lock:
                self._tracked_by_ref[command.order_id] = tracked

            transport_entered = True
            response = self._transport.request("buy", payload, timeout=8.0)
            raw_result = response.get("result")
            nested_id = raw_result.get("id") if isinstance(raw_result, Mapping) else None
            if response.get("status") is False and response.get("id") is None and nested_id is None:
                with self._lock:
                    self._tracked_by_ref.pop(command.order_id, None)
                reason = response.get("reason", response.get("message", "ORDER_REJECTED"))
                return WorkerSubmissionResult(
                    outcome=WorkerOutcome.REJECTED,
                    broker_order_id=None,
                    response_message_id=str(uuid4()),
                    correlation_id=command.correlation_id,
                    causation_id=command.message_id,
                    reason_code=str(reason),
                )

            if response.get("status") is not True:
                raise IQOptionExternalError("IQOPTION_ORDER_RESPONSE_INVALID")
            raw_contract_id = response.get("id", nested_id)
            if (
                isinstance(raw_contract_id, bool)
                or not isinstance(raw_contract_id, (int, str))
                or not str(raw_contract_id).isdigit()
                or int(str(raw_contract_id)) <= 0
            ):
                raise IQOptionExternalError("IQOPTION_ORDER_RESPONSE_INVALID")
            contract_id = str(raw_contract_id)
            tracked.broker_order_id = contract_id
            with self._lock:
                self._tracked[contract_id] = tracked

            return WorkerSubmissionResult(
                outcome=WorkerOutcome.ACCEPTED,
                broker_order_id=contract_id,
                response_message_id=str(uuid4()),
                correlation_id=command.correlation_id,
                causation_id=command.message_id,
                reason_code=None,
            )

        except (IQOptionWorkerError, IQOptionExternalError) as exc:
            if transport_entered and not getattr(exc, "submission_not_sent", False):
                outcome = WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND
            else:
                outcome = WorkerOutcome.REJECTED
                with self._lock:
                    self._tracked_by_ref.pop(command.order_id, None)
            return WorkerSubmissionResult(
                outcome=outcome,
                broker_order_id=None,
                response_message_id=str(uuid4()),
                correlation_id=command.correlation_id,
                causation_id=command.message_id,
                reason_code=exc.reason_code,
            )
        except Exception as exc:
            return WorkerSubmissionResult(
                outcome=WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND,
                broker_order_id=None,
                response_message_id=str(uuid4()),
                correlation_id=command.correlation_id,
                causation_id=command.message_id,
                reason_code=(
                    "IQOPTION_REQUEST_TIMEOUT"
                    if isinstance(exc, (OSError, TimeoutError))
                    else "IQOPTION_ORDER_RESPONSE_INVALID"
                ),
            )

    def drain_contract_events(self, timeout: float = 0.05) -> int:
        count = 0
        while True:
            raw = self._transport.receive_contract(timeout=timeout if count == 0 else 0.001)
            if raw is None:
                break
            msg = raw.get("msg", raw)
            if isinstance(msg, dict):
                event_name = str(raw.get("name", msg.get(IQOPTION_EVENT_NAME_KEY, ""))).lower()
                source = {
                    "option-opened": IQOptionResultSource.OPTION_OPENED,
                    "option-closed": IQOptionResultSource.OPTION_CLOSED,
                }.get(event_name)
                event = self.process_raw_contract_message(msg, result_source=source)
                if event is not None:
                    count += 1
        return count

    def process_raw_contract_message(
        self,
        msg: Mapping[str, Any],
        *,
        result_source: IQOptionResultSource | None = None,
    ) -> BrokerOrderEvent | None:
        contract_id = str(msg.get("id", msg.get("option_id", msg.get("contract_id", ""))))
        client_order_id = str(msg.get("client_order_id", ""))

        with self._lock:
            tracked = self._tracked.get(contract_id) or self._tracked_by_ref.get(client_order_id)
            if tracked is None:
                return None

        if result_source is None:
            return None
        try:
            financial_result = parse_iqoption_financial_result(
                msg,
                result_source,
                expected_stake_minor=tracked.amount.minor_units,
            )
        except IQOptionResultError:
            # Keep tracking the order. A later authoritative event or status
            # query may still provide the missing finality/financial fields.
            return None

        external_status = financial_result.external_status
        result_minor = financial_result.realized_pnl_minor
        is_settled = external_status is ExternalOrderStatus.SETTLED
        result_currency = tracked.amount.currency if is_settled else None

        now_iso = datetime.now(UTC).isoformat()
        sequence = tracked.next_sequence()
        canonical: dict[str, Any] = {
            "event_id": str(uuid4()),
            "event_version": 1,
            "broker": Broker.IQ_OPTION.value,
            "account_id": tracked.account_id,
            "client_order_ref": tracked.client_order_ref,
            "broker_order_id": contract_id or tracked.broker_order_id or "0",
            "correlation_id": tracked.correlation_id,
            "external_sequence": sequence,
            "external_status": external_status.value,
            "occurred_at": now_iso,
            "observed_at": now_iso,
            "product": tracked.product,
            "symbol": tracked.symbol,
            "direction": tracked.direction.value,
            "amount_minor": tracked.amount.minor_units,
            "currency": tracked.amount.currency,
            "result_minor": result_minor,
            "result_currency": result_currency,
        }
        evidence_hash = BrokerOrderEvent.evidence_hash_for_payload(canonical)
        event = BrokerOrderEvent.from_payload({**canonical, "evidence_hash": evidence_hash})

        enqueued = False
        try:
            self._events.put_nowait(event)
            enqueued = True
        except queue.Full:
            with self._lock:
                self._contract_events_overflow_total += 1
                self._reconciliation_required = True

        if is_settled and enqueued:
            with self._lock:
                if contract_id:
                    self._tracked.pop(contract_id, None)
                if tracked.client_order_ref:
                    self._tracked_by_ref.pop(tracked.client_order_ref, None)

        return event if enqueued else None

    def next_queued_event(self, timeout: float = 0.0) -> BrokerOrderEvent | None:
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def contract_events_overflow_total(self) -> int:
        with self._lock:
            return self._contract_events_overflow_total

    @property
    def reconciliation_required(self) -> bool:
        with self._lock:
            return self._reconciliation_required

    def get_tracked_by_contract_id(self, contract_id: str) -> TrackedIQOptionOrder | None:
        with self._lock:
            return self._tracked.get(contract_id)

    def get_tracked_by_order_id(self, order_id: str) -> TrackedIQOptionOrder | None:
        with self._lock:
            return self._tracked_by_ref.get(order_id)
