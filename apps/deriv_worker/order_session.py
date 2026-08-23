from __future__ import annotations

import contextlib
import queue
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from apps.deriv_worker.request_allowlist import DerivOperation
from apps.deriv_worker.schema import DerivErrorCategory, DerivWorkerError
from apps.deriv_worker.websocket_client import DerivReadTransport
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
class TrackedDerivOrder:
    order_id: str
    correlation_id: str
    intent_id: str
    symbol: str
    direction: Direction
    amount: Money
    contract_id: str
    account_id: str
    buy_price_minor: int
    buy_time_utc: datetime
    sequence_counter: int = 0

    def next_sequence(self) -> int:
        self.sequence_counter += 1
        return self.sequence_counter


class DerivOrderSession:
    """Manages order submission and lifecycle events for Deriv Demo accounts."""

    def __init__(
        self,
        transport: DerivReadTransport,
        account_id: str,
        *,
        demo_authenticated: bool = True,
        timeout_seconds: float = 3.0,
        event_queue_size: int = 128,
    ) -> None:
        if not account_id:
            raise ValueError("account_id is required")
        if not demo_authenticated:
            raise DerivWorkerError(
                DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
                "DERIV_REAL_ACCOUNT_FORBIDDEN",
            )
        self._transport = transport
        self._account_id = account_id
        self._demo_authenticated = demo_authenticated
        self._timeout_seconds = timeout_seconds
        self._tracked: dict[str, TrackedDerivOrder] = {}
        self._tracked_by_order_id: dict[str, TrackedDerivOrder] = {}
        self._events: queue.Queue[BrokerOrderEvent] = queue.Queue(maxsize=event_queue_size)
        self._lock = threading.Lock()
        self._subscribed_contracts: set[str] = set()

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def demo_authenticated(self) -> bool:
        return self._demo_authenticated

    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult:
        if not self._demo_authenticated:
            raise DerivWorkerError(
                DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
                "DERIV_REAL_ACCOUNT_FORBIDDEN",
            )
        if command.broker is not Broker.DERIV:
            raise DerivWorkerError(
                DerivErrorCategory.INVALID_REQUEST,
                "DERIV_INVALID_BROKER",
            )

        stake = Decimal(command.amount.minor_units) / Decimal(100)
        payload: dict[str, object] = {
            "buy": 1,
            "price": str(stake),
            "parameters": {
                "amount": str(stake),
                "basis": "stake",
                "contract_type": command.direction.value,
                "currency": command.amount.currency,
                "duration": 1,
                "duration_unit": "m",
                "symbol": command.symbol,
            },
            "passthrough": {
                "order_id": command.order_id,
                "correlation_id": command.correlation_id,
                "intent_id": command.intent_id,
            },
        }

        try:
            response = self._transport.request(
                DerivOperation.BUY,
                payload,
                timeout=self._timeout_seconds,
            )
        except DerivWorkerError as exc:
            if exc.category in (
                DerivErrorCategory.NETWORK_ERROR,
                DerivErrorCategory.RATE_LIMITED,
            ):
                return WorkerSubmissionResult(
                    outcome=WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND,
                    broker_order_id=None,
                    response_message_id=str(uuid4()),
                    correlation_id=command.correlation_id,
                    causation_id=command.message_id,
                    reason_code=exc.reason_code,
                )
            return WorkerSubmissionResult(
                outcome=WorkerOutcome.REJECTED,
                broker_order_id=None,
                response_message_id=str(uuid4()),
                correlation_id=command.correlation_id,
                causation_id=command.message_id,
                reason_code=exc.reason_code,
            )
        except (OSError, TimeoutError):
            return WorkerSubmissionResult(
                outcome=WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND,
                broker_order_id=None,
                response_message_id=str(uuid4()),
                correlation_id=command.correlation_id,
                causation_id=command.message_id,
                reason_code="DERIV_REQUEST_TIMEOUT",
            )

        buy_data = response.get("buy")
        if not isinstance(buy_data, dict):
            error = response.get("error")
            reason = "DERIV_BUY_REJECTED"
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                reason = str(error["code"])
            return WorkerSubmissionResult(
                outcome=WorkerOutcome.REJECTED,
                broker_order_id=None,
                response_message_id=str(uuid4()),
                correlation_id=command.correlation_id,
                causation_id=command.message_id,
                reason_code=reason,
            )

        contract_id_raw = buy_data.get("contract_id")
        if contract_id_raw is None:
            return WorkerSubmissionResult(
                outcome=WorkerOutcome.REJECTED,
                broker_order_id=None,
                response_message_id=str(uuid4()),
                correlation_id=command.correlation_id,
                causation_id=command.message_id,
                reason_code="DERIV_CONTRACT_ID_MISSING",
            )
        contract_id = str(contract_id_raw)

        buy_price_decimal = Decimal(str(buy_data.get("buy_price", stake)))
        buy_price_minor = int(buy_price_decimal * 100)

        tracked = TrackedDerivOrder(
            order_id=command.order_id,
            correlation_id=command.correlation_id,
            intent_id=command.intent_id,
            symbol=command.symbol,
            direction=command.direction,
            amount=command.amount,
            contract_id=contract_id,
            account_id=command.account_id,
            buy_price_minor=buy_price_minor,
            buy_time_utc=datetime.now(UTC),
        )
        with self._lock:
            self._tracked[contract_id] = tracked
            self._tracked_by_order_id[command.order_id] = tracked

        self._subscribe_contract(contract_id)

        return WorkerSubmissionResult(
            outcome=WorkerOutcome.ACCEPTED,
            broker_order_id=contract_id,
            response_message_id=str(uuid4()),
            correlation_id=command.correlation_id,
            causation_id=command.message_id,
        )

    def _subscribe_contract(self, contract_id: str) -> None:
        if contract_id in self._subscribed_contracts:
            return
        try:
            self._transport.request(
                DerivOperation.PROPOSAL_OPEN_CONTRACT,
                {
                    "proposal_open_contract": 1,
                    "contract_id": int(contract_id),
                    "subscribe": 1,
                },
                timeout=self._timeout_seconds,
            )
            self._subscribed_contracts.add(contract_id)
        except Exception:
            pass

    def drain_contract_events(self, timeout: float = 0.0) -> int:
        count = 0
        raw = self._transport.receive_contract(timeout=timeout)
        while raw is not None:
            self.process_raw_contract_message(raw)
            count += 1
            raw = self._transport.receive_contract(timeout=0.0)
        return count

    def process_raw_contract_message(self, raw: Mapping[str, object]) -> BrokerOrderEvent | None:
        poc = raw.get("proposal_open_contract")
        if not isinstance(poc, dict):
            return None
        contract_id_raw = poc.get("contract_id")
        if contract_id_raw is None:
            return None
        contract_id = str(contract_id_raw)

        with self._lock:
            tracked = self._tracked.get(contract_id)

        if tracked is None:
            passthrough = poc.get("passthrough") or raw.get("passthrough")
            if isinstance(passthrough, dict):
                order_id = str(passthrough.get("order_id", ""))
                correlation_id = str(passthrough.get("correlation_id", ""))
                intent_id = str(passthrough.get("intent_id", ""))
                symbol = str(poc.get("underlying", ""))
                direction_str = str(poc.get("contract_type", "CALL"))
                direction = Direction.PUT if direction_str.upper() == "PUT" else Direction.CALL
                buy_price = Decimal(str(poc.get("buy_price", 0)))
                currency = str(poc.get("currency", "USD"))
                if order_id and correlation_id:
                    tracked = TrackedDerivOrder(
                        order_id=order_id,
                        correlation_id=correlation_id,
                        intent_id=intent_id,
                        symbol=symbol,
                        direction=direction,
                        amount=Money(int(buy_price * 100), currency),
                        contract_id=contract_id,
                        account_id=self._account_id,
                        buy_price_minor=int(buy_price * 100),
                        buy_time_utc=datetime.now(UTC),
                    )
                    with self._lock:
                        self._tracked[contract_id] = tracked
                        self._tracked_by_order_id[order_id] = tracked

        if tracked is None:
            return None

        status = str(poc.get("status", "open")).lower()
        is_sold = int(str(poc.get("is_sold", 0))) == 1
        is_settled = is_sold or status in ("won", "lost", "sold")

        sequence = tracked.next_sequence()
        now = datetime.now(UTC)

        if is_settled:
            external_status = ExternalOrderStatus.SETTLED
            profit_val = poc.get("profit")
            if profit_val is not None:
                profit_decimal = Decimal(str(profit_val))
            else:
                payout_val = Decimal(str(poc.get("payout", 0)))
                buy_val = Decimal(str(poc.get("buy_price", tracked.buy_price_minor / 100)))
                profit_decimal = payout_val - buy_val
            result_minor = int(profit_decimal * 100)
            result_currency = str(poc.get("currency", tracked.amount.currency)).upper()
        else:
            external_status = ExternalOrderStatus.OPEN
            result_minor = None
            result_currency = None

        canonical: dict[str, object] = {
            "event_id": str(uuid4()),
            "event_version": 1,
            "broker": Broker.DERIV.value,
            "account_id": tracked.account_id,
            "client_order_ref": tracked.order_id,
            "broker_order_id": contract_id,
            "correlation_id": tracked.correlation_id,
            "external_sequence": sequence,
            "external_status": external_status.value,
            "occurred_at": now.isoformat(),
            "observed_at": now.isoformat(),
            "product": "DIGITAL_OPTION",
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
                self._tracked.pop(contract_id, None)

        return event

    def next_queued_event(self, timeout: float = 0.0) -> BrokerOrderEvent | None:
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_tracked_by_contract_id(self, contract_id: str) -> TrackedDerivOrder | None:
        with self._lock:
            return self._tracked.get(contract_id)

    def get_tracked_by_order_id(self, order_id: str) -> TrackedDerivOrder | None:
        with self._lock:
            return self._tracked_by_order_id.get(order_id)
