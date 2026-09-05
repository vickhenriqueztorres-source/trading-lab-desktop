from __future__ import annotations

import contextlib
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

    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult:
        transport_entered = False
        try:
            validate_iqoption_order_command(command)
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
                event = self.process_raw_contract_message(msg)
                if event is not None:
                    count += 1
        return count

    def process_raw_contract_message(self, msg: Mapping[str, Any]) -> BrokerOrderEvent | None:
        contract_id = str(msg.get("id", msg.get("option_id", msg.get("contract_id", ""))))
        client_order_id = str(msg.get("client_order_id", ""))
        status_str = str(msg.get("status", "")).lower()
        win_str = str(msg.get("win", "")).lower()

        with self._lock:
            tracked = self._tracked.get(contract_id) or self._tracked_by_ref.get(client_order_id)
            if tracked is None:
                return None

        if status_str == "open" or win_str == "equal":
            external_status = ExternalOrderStatus.OPEN
            result_minor = None
            result_currency = None
            is_settled = False
        elif status_str in ("win", "loose") or win_str in ("win", "loose"):
            external_status = ExternalOrderStatus.SETTLED
            is_settled = True
            win_amount_str = str(msg.get("win_amount", msg.get("profit_amount", "0.00")))
            win_decimal = Decimal(win_amount_str)
            stake_decimal = Decimal(tracked.amount.minor_units) / Decimal(100)
            pnl_decimal = win_decimal - stake_decimal
            result_minor = int(pnl_decimal * Decimal(100))
            result_currency = tracked.amount.currency
        else:
            return None

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

        with contextlib.suppress(queue.Full):
            self._events.put_nowait(event)

        if is_settled:
            with self._lock:
                if contract_id:
                    self._tracked.pop(contract_id, None)
                if tracked.client_order_ref:
                    self._tracked_by_ref.pop(tracked.client_order_ref, None)

        return event

    def next_queued_event(self, timeout: float = 0.0) -> BrokerOrderEvent | None:
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None
