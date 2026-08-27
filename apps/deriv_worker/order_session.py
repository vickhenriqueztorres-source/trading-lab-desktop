from __future__ import annotations

import contextlib
import queue
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
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
    product: str = "DIGITAL_OPTION"
    prediction_digit: int | None = None
    sequence_counter: int = 0

    def next_sequence(self) -> int:
        self.sequence_counter += 1
        return self.sequence_counter


class DerivLiveOrderSession:
    """Manages order submission and lifecycle events for an authenticated Deriv account."""

    def __init__(
        self,
        transport: DerivReadTransport,
        account_id: str,
        *,
        demo_authenticated: bool = True,
        account_type: str = "demo",
        timeout_seconds: float = 3.0,
        event_queue_size: int = 128,
    ) -> None:
        if not account_id:
            raise ValueError("account_id is required")
        if account_type != "demo" or not demo_authenticated:
            raise DerivWorkerError(
                DerivErrorCategory.ACCOUNT_MODE_FORBIDDEN,
                "DERIV_REAL_ACCOUNT_FORBIDDEN",
            )
        self._transport = transport
        self._account_id = account_id
        self._demo_authenticated = demo_authenticated
        self._account_type = account_type
        self._timeout_seconds = timeout_seconds
        self._tracked: dict[str, TrackedDerivOrder] = {}
        self._tracked_by_order_id: dict[str, TrackedDerivOrder] = {}
        self._events: queue.Queue[BrokerOrderEvent] = queue.Queue(maxsize=event_queue_size)
        self._lock = threading.Lock()
        self._subscribed_contracts: set[str] = set()
        self._contract_subscription_ids: dict[str, str] = {}

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def demo_authenticated(self) -> bool:
        return self._demo_authenticated

    @property
    def trading_authenticated(self) -> bool:
        return self._demo_authenticated

    @property
    def account_type(self) -> str:
        return self._account_type

    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult:
        return self._submit_buy_order(command, self._transport)

    def submit_digit_diff_order(
        self, command: OrderCommand, prediction_digit: int
    ) -> WorkerSubmissionResult:
        return self._submit_buy_order(
            replace(command, prediction_digit=prediction_digit), self._transport
        )

    def submit_buy_order(
        self,
        command: OrderCommand,
        client: DerivReadTransport,
    ) -> WorkerSubmissionResult:
        """Submit a buy through an explicitly supplied authenticated Deriv client."""

        return self._submit_buy_order(command, client)

    def _submit_buy_order(
        self,
        command: OrderCommand,
        transport: DerivReadTransport,
    ) -> WorkerSubmissionResult:
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
        if command.account_id != self._account_id:
            raise DerivWorkerError(
                DerivErrorCategory.INVALID_REQUEST,
                "DERIV_ACCOUNT_SCOPE_MISMATCH",
            )
        if command.deadline_at <= datetime.now(UTC):
            return WorkerSubmissionResult(
                outcome=WorkerOutcome.REJECTED,
                broker_order_id=None,
                response_message_id=str(uuid4()),
                correlation_id=command.correlation_id,
                causation_id=command.message_id,
                reason_code="ORDER_COMMAND_EXPIRED",
            )

        stake = Decimal(command.amount.minor_units) / Decimal(100)
        product = command.product.upper()
        digit_products = {"DIGITDIFF", "DIGITOVER", "DIGITUNDER", "DIGITEVEN", "DIGITODD"}
        if product in digit_products:
            barrier_required = product in {"DIGITDIFF", "DIGITOVER", "DIGITUNDER"}
            if barrier_required and command.prediction_digit is None:
                return WorkerSubmissionResult(
                    outcome=WorkerOutcome.REJECTED,
                    broker_order_id=None,
                    response_message_id=str(uuid4()),
                    correlation_id=command.correlation_id,
                    causation_id=command.message_id,
                    reason_code=(
                        "DERIV_DIGIT_PREDICTION_REQUIRED"
                        if product == "DIGITDIFF"
                        else "DERIV_DIGIT_BARRIER_REQUIRED"
                    ),
                )
            digit_proposal_payload: dict[str, object] = {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": product,
                "currency": command.amount.currency,
                "duration": 1,
                "duration_unit": "t",
                "underlying_symbol": command.symbol,
                "passthrough": {
                    "order_id": command.order_id,
                    "correlation_id": command.correlation_id,
                },
            }
            if barrier_required:
                digit_proposal_payload["barrier"] = str(command.prediction_digit)
            return self._proposal_then_buy(command, digit_proposal_payload, transport, stake)

        proposal_payload: dict[str, object] = {
            "proposal": 1,
            "amount": stake,
            "basis": "stake",
            "contract_type": command.direction.value,
            "currency": command.amount.currency,
            "duration": command.duration,
            "duration_unit": command.duration_unit,
            "underlying_symbol": command.symbol,
            "passthrough": {
                "order_id": command.order_id,
                "correlation_id": command.correlation_id,
            },
        }

        return self._proposal_then_buy(command, proposal_payload, transport, stake)

    def _proposal_then_buy(
        self,
        command: OrderCommand,
        proposal_payload: Mapping[str, object],
        transport: DerivReadTransport,
        stake: Decimal,
    ) -> WorkerSubmissionResult:
        try:
            proposal_response = transport.request(
                DerivOperation.PROPOSAL,
                proposal_payload,
                timeout=self._timeout_seconds,
            )
        except DerivWorkerError as exc:
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
                outcome=WorkerOutcome.REJECTED,
                broker_order_id=None,
                response_message_id=str(uuid4()),
                correlation_id=command.correlation_id,
                causation_id=command.message_id,
                reason_code="DERIV_PROPOSAL_TIMEOUT",
            )

        proposal = proposal_response.get("proposal")
        if not isinstance(proposal, dict) or not isinstance(proposal.get("id"), str):
            error = proposal_response.get("error")
            reason = "DERIV_PROPOSAL_REJECTED"
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

        payload: dict[str, object] = {
            "buy": str(proposal["id"]),
            "price": stake,
            "passthrough": {
                "order_id": command.order_id,
                "correlation_id": command.correlation_id,
            },
        }

        return self._execute_buy_payload(command, payload, transport, stake)

    def _execute_buy_payload(
        self,
        command: OrderCommand,
        payload: Mapping[str, object],
        transport: DerivReadTransport,
        stake: Decimal,
    ) -> WorkerSubmissionResult:
        try:
            response = transport.request(DerivOperation.BUY, payload, timeout=self._timeout_seconds)
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
        buy_price_minor = self._money_to_minor_units(buy_price_decimal)

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
            product=command.product.upper(),
            prediction_digit=command.prediction_digit,
        )
        with self._lock:
            self._tracked[contract_id] = tracked
            self._tracked_by_order_id[command.order_id] = tracked

        self._subscribe_contract(contract_id, transport)

        return WorkerSubmissionResult(
            outcome=WorkerOutcome.ACCEPTED,
            broker_order_id=contract_id,
            response_message_id=str(uuid4()),
            correlation_id=command.correlation_id,
            causation_id=command.message_id,
        )

    def _subscribe_contract(
        self,
        contract_id: str,
        transport: DerivReadTransport | None = None,
    ) -> None:
        if contract_id in self._subscribed_contracts:
            return
        try:
            response = (transport or self._transport).request(
                DerivOperation.PROPOSAL_OPEN_CONTRACT,
                {
                    "proposal_open_contract": 1,
                    "contract_id": int(contract_id),
                    "subscribe": 1,
                },
                timeout=self._timeout_seconds,
            )
            self._subscribed_contracts.add(contract_id)
            subscription = response.get("subscription")
            if isinstance(subscription, dict) and isinstance(subscription.get("id"), str):
                self._contract_subscription_ids[contract_id] = str(subscription["id"])
        except Exception:
            pass

    def _forget_contract(self, contract_id: str) -> None:
        subscription_id = self._contract_subscription_ids.pop(contract_id, None)
        self._subscribed_contracts.discard(contract_id)
        if subscription_id is None:
            return
        with contextlib.suppress(Exception):
            self._transport.request(
                DerivOperation.FORGET,
                {"forget": subscription_id},
                timeout=self._timeout_seconds,
            )

    def drain_contract_events(self, timeout: float = 0.0) -> int:
        count = 0
        raw = self._transport.receive_contract(timeout=timeout)
        while raw is not None:
            self.process_raw_contract_message(raw)
            count += 1
            raw = self._transport.receive_contract(timeout=0.0)
        return count

    def on_proposal_open_contract_message(
        self, raw: Mapping[str, object]
    ) -> BrokerOrderEvent | None:
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
                intent_id = str(passthrough.get("intent_id", "reconciled-live-order"))
                symbol = str(poc.get("underlying", ""))
                contract_type = str(poc.get("contract_type", "CALL")).upper()
                direction_str = contract_type
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
                        amount=Money(self._money_to_minor_units(buy_price), currency),
                        contract_id=contract_id,
                        account_id=self._account_id,
                        buy_price_minor=self._money_to_minor_units(buy_price),
                        buy_time_utc=datetime.now(UTC),
                        product=(
                            contract_type
                            if contract_type
                            in {
                                "DIGITDIFF",
                                "DIGITOVER",
                                "DIGITUNDER",
                                "DIGITEVEN",
                                "DIGITODD",
                            }
                            else "DIGITAL_OPTION"
                        ),
                        prediction_digit=(
                            int(str(poc["barrier"])) if poc.get("barrier") is not None else None
                        ),
                    )
                    with self._lock:
                        self._tracked[contract_id] = tracked
                        self._tracked_by_order_id[order_id] = tracked

        if tracked is None:
            return None

        status = str(poc.get("status", "open")).lower()
        is_sold = int(str(poc.get("is_sold", 0))) == 1
        is_expired = int(str(poc.get("is_expired", 0))) == 1
        is_settled = is_expired or is_sold or status in ("won", "lost", "sold")

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
            result_minor = self._money_to_minor_units(profit_decimal)
            result_currency = str(poc.get("currency", tracked.amount.currency)).upper()
            seconds_remaining = None
            exit_tick = poc.get("exit_tick", poc.get("exit_spot"))
            current_spot = str(exit_tick) if exit_tick is not None else None
            if tracked.product in {
                "DIGITDIFF",
                "DIGITOVER",
                "DIGITUNDER",
                "DIGITEVEN",
                "DIGITODD",
            }:
                if exit_tick is None:
                    raise DerivWorkerError(
                        DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                        "DERIV_DIGIT_EXIT_TICK_MISSING",
                    )
                exit_decimal = Decimal(str(exit_tick))
                if not exit_decimal.is_finite():
                    raise DerivWorkerError(
                        DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                        "DERIV_DIGIT_EXIT_TICK_INVALID",
                    )
                exit_digit = int(exit_decimal.as_tuple().digits[-1])
                if tracked.product == "DIGITDIFF" and tracked.prediction_digit is not None:
                    digit_won = exit_digit != tracked.prediction_digit
                elif tracked.product == "DIGITOVER" and tracked.prediction_digit is not None:
                    digit_won = exit_digit > tracked.prediction_digit
                elif tracked.product == "DIGITUNDER" and tracked.prediction_digit is not None:
                    digit_won = exit_digit < tracked.prediction_digit
                elif tracked.product == "DIGITEVEN":
                    digit_won = exit_digit % 2 == 0
                elif tracked.product == "DIGITODD":
                    digit_won = exit_digit % 2 == 1
                else:
                    raise DerivWorkerError(
                        DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                        "DERIV_DIGIT_CONTRACT_UNSUPPORTED",
                    )
                official_won = status == "won" or profit_decimal > 0
                if digit_won != official_won:
                    raise DerivWorkerError(
                        DerivErrorCategory.SCHEMA_INCOMPATIBLE,
                        "DERIV_DIGIT_SETTLEMENT_CONFLICT",
                    )
        else:
            external_status = ExternalOrderStatus.OPEN
            result_minor = None
            result_currency = None
            expiry_raw = poc.get("date_expiry")
            seconds_remaining = (
                max(0, int(expiry_raw) - int(now.timestamp()))
                if isinstance(expiry_raw, int)
                else None
            )
            spot_raw = poc.get("current_spot", poc.get("bid_price"))
            current_spot = str(spot_raw) if spot_raw is not None else None

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
            "product": tracked.product,
            "symbol": tracked.symbol,
            "direction": tracked.direction.value,
            "amount_minor": tracked.amount.minor_units,
            "currency": tracked.amount.currency,
            "result_minor": result_minor,
            "result_currency": result_currency,
        }
        if seconds_remaining is not None:
            canonical["seconds_remaining"] = seconds_remaining
        if current_spot is not None:
            canonical["current_spot"] = current_spot
        evidence_hash = BrokerOrderEvent.evidence_hash_for_payload(canonical)
        event = BrokerOrderEvent.from_payload({**canonical, "evidence_hash": evidence_hash})

        with contextlib.suppress(queue.Full):
            self._events.put_nowait(event)

        if is_settled:
            with self._lock:
                self._tracked.pop(contract_id, None)
                self._tracked_by_order_id.pop(tracked.order_id, None)
            self._forget_contract(contract_id)

        return event

    def process_raw_contract_message(self, raw: Mapping[str, object]) -> BrokerOrderEvent | None:
        return self.on_proposal_open_contract_message(raw)

    @staticmethod
    def _money_to_minor_units(value: Decimal) -> int:
        cents = (value * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
        return int(cents)

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


# Backward-compatible name retained for the existing worker/server boundary.
DerivOrderSession = DerivLiveOrderSession
