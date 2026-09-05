from __future__ import annotations

import hashlib
import queue
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from packages.domain.market import (
    BrokerAccountBalance,
    BrokerCapabilities,
    BrokerClockSnapshot,
    BrokerProposalQuote,
    ContractMetadata,
    MarketCandle,
    MarketHistoryBatch,
    MarketSymbol,
    MarketTick,
)
from packages.domain.models import Broker, BrokerOrderEvent, Money, OrderCommand, OrderStatusQuery
from packages.market_data import DigitFrequencySnapshot
from packages.observability.events import EventSink, NullEventSink
from packages.protocol.codec import encode_envelope
from packages.protocol.envelope import EndpointRole, Envelope, MessageType
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.protocol.messages import (
    OrderStatusResult,
    WorkerCapabilities,
    WorkerSubmissionResult,
    parse_broker_balance_response,
    parse_broker_capabilities_response,
    parse_broker_clock_response,
    parse_market_contracts_response,
    parse_market_history_response,
    parse_market_symbols_response,
    parse_market_tick,
    parse_order_event,
    parse_order_response,
    parse_order_status_response,
)
from packages.protocol.transport import FramedSocket
from packages.protocol.version import PROTOCOL_VERSION


class DeliveryCertainty(StrEnum):
    NOT_SENT = "NOT_SENT"
    POSSIBLY_SENT = "POSSIBLY_SENT"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"


class WorkerDispatchError(RuntimeError):
    def __init__(
        self,
        code: ProtocolErrorCode,
        delivery: DeliveryCertainty,
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.delivery = delivery


def _decimal_money_to_minor_units(value: Decimal, currency: str) -> int:
    if currency != "USD":
        raise WorkerDispatchError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            DeliveryCertainty.NOT_SENT,
            "broker quote currency is unsupported",
        )
    cents = value * Decimal(100)
    if cents != cents.to_integral_value():
        raise WorkerDispatchError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            DeliveryCertainty.NOT_SENT,
            "broker quote monetary value has unsupported precision",
        )
    return int(cents)


def _proposal_quote_from_payload(
    payload: Mapping[str, object],
    received_monotonic: float,
) -> BrokerProposalQuote:
    required = (
        "broker_symbol",
        "contract_type",
        "proposal_id",
        "ask_price",
        "payout",
        "net_profit_ratio",
    )
    if any(name not in payload for name in required):
        raise WorkerDispatchError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            DeliveryCertainty.NOT_SENT,
            "broker quote payload is incomplete",
        )
    broker_symbol = payload["broker_symbol"]
    contract_type = payload["contract_type"]
    proposal_id = payload["proposal_id"]
    currency = payload.get("currency", "USD")
    barrier = payload.get("barrier")
    if (
        not isinstance(broker_symbol, str)
        or not isinstance(contract_type, str)
        or not isinstance(proposal_id, str)
        or not isinstance(currency, str)
        or (barrier is not None and (type(barrier) is not int or not 0 <= barrier <= 9))
    ):
        raise WorkerDispatchError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            DeliveryCertainty.NOT_SENT,
            "broker quote identity is invalid",
        )
    try:
        ask = Decimal(str(payload["ask_price"]))
        payout = Decimal(str(payload["payout"]))
        ratio = Decimal(str(payload["net_profit_ratio"]))
    except (InvalidOperation, ValueError) as exc:
        raise WorkerDispatchError(
            ProtocolErrorCode.IPC_INVALID_ENVELOPE,
            DeliveryCertainty.NOT_SENT,
            "broker quote decimal payload is invalid",
        ) from exc
    return BrokerProposalQuote(
        broker=Broker.DERIV,
        broker_symbol=broker_symbol,
        contract_type=contract_type.upper(),
        barrier=barrier,
        ask_price=Money(_decimal_money_to_minor_units(ask, currency), currency),
        payout=Money(_decimal_money_to_minor_units(payout, currency), currency),
        proposal_id=proposal_id,
        received_monotonic=received_monotonic,
        payout_return_ratio=ratio,
    )


class OrderSubmissionPort(Protocol):
    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult: ...


class OrderStatusPort(Protocol):
    def query_order_status(
        self,
        query: OrderStatusQuery,
        *,
        timeout: float | None = None,
    ) -> OrderStatusResult: ...


class WorkerPort(OrderSubmissionPort, OrderStatusPort, Protocol):
    pass


class StatusQueryError(RuntimeError):
    def __init__(self, code: ProtocolErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class MessageReplayGuard:
    """Bounded replay cache; identical replay is idempotent, conflict is fatal."""

    def __init__(self, capacity: int = 1024) -> None:
        if capacity <= 0:
            raise ValueError("replay capacity must be positive")
        self._capacity = capacity
        self._digests: OrderedDict[str, str] = OrderedDict()

    def observe(self, envelope: Envelope) -> bool:
        digest = hashlib.sha256(encode_envelope(envelope)).hexdigest()
        previous = self._digests.get(envelope.message_id)
        if previous is not None:
            if previous != digest:
                raise ProtocolError(
                    ProtocolErrorCode.IPC_MESSAGE_REPLAY_CONFLICT,
                    "message_id was replayed with conflicting content",
                )
            self._digests.move_to_end(envelope.message_id)
            return False
        self._digests[envelope.message_id] = digest
        if len(self._digests) > self._capacity:
            self._digests.popitem(last=False)
        return True


class SocketWorkerClient:
    def __init__(
        self,
        transport: FramedSocket,
        capabilities: WorkerCapabilities,
        *,
        response_timeout: float = 1.0,
        event_queue_size: int = 128,
        max_pending_requests: int = 64,
        event_sink: EventSink | None = None,
        on_disconnect: Callable[[ProtocolErrorCode], None] | None = None,
        worker_role: EndpointRole = EndpointRole.SIMULATED_WORKER,
    ) -> None:
        if response_timeout <= 0 or event_queue_size <= 0 or max_pending_requests <= 0:
            raise ValueError("timeouts and queue sizes must be positive")
        self.capabilities = capabilities
        self._worker_role = worker_role
        self._transport = transport
        self._response_timeout = response_timeout
        self._event_sink = event_sink or NullEventSink()
        self._on_disconnect = on_disconnect
        self._pending_lock = threading.Lock()
        self._pending: dict[str, queue.Queue[Envelope | BaseException]] = {}
        self._pending_slots = threading.BoundedSemaphore(max_pending_requests)
        self._order_events: queue.Queue[Envelope] = queue.Queue(maxsize=event_queue_size)
        self._market_events: queue.Queue[Envelope] = queue.Queue(maxsize=event_queue_size)
        self._replay = MessageReplayGuard()
        self._ready = True
        self._closing = threading.Event()
        self._fatal_error: BaseException | None = None
        self._duplicate_count = 0
        self._reader = threading.Thread(
            target=self._reader_loop,
            name="ipc-worker-reader",
            daemon=True,
        )
        self._reader.start()

    @classmethod
    def handshake(
        cls,
        transport: FramedSocket,
        *,
        timeout_seconds: float,
        event_sink: EventSink | None = None,
        response_timeout: float = 1.0,
        event_queue_size: int = 128,
        on_disconnect: Callable[[ProtocolErrorCode], None] | None = None,
        expected_worker_role: EndpointRole = EndpointRole.SIMULATED_WORKER,
        expected_broker: str = "simulated",
    ) -> SocketWorkerClient:
        sink = event_sink or NullEventSink()
        transport.set_timeout(timeout_seconds)
        message_id = str(uuid4())
        correlation_id = str(uuid4())
        hello = Envelope(
            protocol_version=PROTOCOL_VERSION,
            message_id=message_id,
            correlation_id=correlation_id,
            causation_id=None,
            source=EndpointRole.CORE,
            target=expected_worker_role,
            message_type=MessageType.HELLO,
            created_at_utc=datetime.now(UTC),
            deadline_at=None,
            payload={"client_role": EndpointRole.CORE.value},
        )
        sink.emit("ipc_handshake_started", message_id=message_id, correlation_id=correlation_id)
        try:
            transport.send(hello)
            response = transport.receive()
        except ProtocolError as exc:
            if exc.code is ProtocolErrorCode.IPC_FRAME_TRUNCATED:
                raise ProtocolError(
                    ProtocolErrorCode.IPC_HANDSHAKE_TIMEOUT,
                    "worker handshake timed out",
                ) from exc
            raise
        finally:
            transport.set_timeout(None)
        if (
            response.message_type is not MessageType.HELLO_ACK
            or response.source is not expected_worker_role
            or response.target is not EndpointRole.CORE
            or response.causation_id != message_id
            or response.correlation_id != correlation_id
        ):
            raise ProtocolError(
                ProtocolErrorCode.IPC_ROLE_MISMATCH,
                "invalid HELLO_ACK routing",
            )
        if response.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(
                ProtocolErrorCode.IPC_PROTOCOL_INCOMPATIBLE,
                "worker protocol version is incompatible",
            )
        capabilities = WorkerCapabilities.from_payload(response.payload)
        if capabilities.broker != expected_broker:
            raise ProtocolError(
                ProtocolErrorCode.IPC_PROTOCOL_INCOMPATIBLE,
                "worker capabilities identify an unexpected broker",
            )
        sink.emit(
            "ipc_handshake_completed",
            message_id=response.message_id,
            correlation_id=response.correlation_id,
            worker_type=expected_worker_role.value,
            broker=capabilities.broker,
        )
        return cls(
            transport,
            capabilities,
            response_timeout=response_timeout,
            event_queue_size=event_queue_size,
            event_sink=sink,
            on_disconnect=on_disconnect,
            worker_role=expected_worker_role,
        )

    @property
    def is_ready(self) -> bool:
        return self._ready and self._fatal_error is None

    @property
    def duplicate_count(self) -> int:
        return self._duplicate_count

    @property
    def pending_request_count(self) -> int:
        """Return in-flight IPC requests without exposing their contents."""

        with self._pending_lock:
            return len(self._pending)

    def _reader_loop(self) -> None:
        try:
            while not self._closing.is_set():
                envelope = self._transport.receive()
                if (
                    envelope.protocol_version != PROTOCOL_VERSION
                    or envelope.source is not self._worker_role
                    or envelope.target is not EndpointRole.CORE
                ):
                    raise ProtocolError(
                        ProtocolErrorCode.IPC_ROLE_MISMATCH,
                        "worker response has invalid version or roles",
                    )
                if not self._replay.observe(envelope):
                    self._duplicate_count += 1
                    continue
                routed = False
                if envelope.causation_id is not None:
                    with self._pending_lock:
                        response_queue = self._pending.get(envelope.causation_id)
                    if response_queue is not None:
                        try:
                            response_queue.put_nowait(envelope)
                        except queue.Full as exc:
                            raise ProtocolError(
                                ProtocolErrorCode.IPC_BACKPRESSURE,
                                "bounded response queue is saturated",
                            ) from exc
                        routed = True
                if not routed:
                    if envelope.message_type is MessageType.MARKET_TICK_EVENT:
                        event_queue = self._market_events
                    elif envelope.message_type is MessageType.ORDER_EVENT:
                        event_queue = self._order_events
                    else:
                        self._event_sink.emit(
                            "late_worker_response_ignored",
                            message_id=envelope.message_id,
                            correlation_id=envelope.correlation_id,
                            message_type=envelope.message_type.value,
                        )
                        continue
                    try:
                        event_queue.put(envelope, timeout=0.1)
                    except queue.Full as exc:
                        raise ProtocolError(
                            ProtocolErrorCode.IPC_BACKPRESSURE,
                            "bounded financial event queue is saturated",
                        ) from exc
        except BaseException as exc:
            if not self._closing.is_set():
                self._fail(exc)

    def _fail(self, exc: BaseException) -> None:
        self._ready = False
        self._fatal_error = exc
        code = exc.code if isinstance(exc, ProtocolError) else ProtocolErrorCode.IPC_CONNECTION_LOST
        self._event_sink.emit("worker_disconnected", reason_code=code.value)
        with self._pending_lock:
            pending = tuple(self._pending.values())
        for response_queue in pending:
            with suppress(queue.Full):
                response_queue.put_nowait(exc)
        if self._on_disconnect is not None:
            self._on_disconnect(code)

    def _request(self, envelope: Envelope, timeout: float) -> Envelope:
        if not self.is_ready:
            raise WorkerDispatchError(
                ProtocolErrorCode.WORKER_NOT_READY,
                DeliveryCertainty.NOT_SENT,
                "worker is not ready",
            )
        if not self._pending_slots.acquire(blocking=False):
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_BACKPRESSURE,
                DeliveryCertainty.NOT_SENT,
                "bounded pending request capacity is saturated",
            )
        try:
            return self._request_with_slot(envelope, timeout)
        finally:
            self._pending_slots.release()

    def _request_with_slot(self, envelope: Envelope, timeout: float) -> Envelope:
        response_queue: queue.Queue[Envelope | BaseException] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[envelope.message_id] = response_queue
        send_started = False
        try:
            if envelope.message_type is MessageType.ORDER_SUBMIT:
                self._event_sink.emit(
                    "order_command_send_started",
                    message_id=envelope.message_id,
                    correlation_id=envelope.correlation_id,
                )
            send_started = True
            self._transport.send(envelope)
            if envelope.message_type is MessageType.ORDER_SUBMIT:
                self._event_sink.emit(
                    "order_command_sent",
                    message_id=envelope.message_id,
                    correlation_id=envelope.correlation_id,
                )
            item = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise WorkerDispatchError(
                ProtocolErrorCode.ORDER_DISPATCH_AMBIGUOUS,
                DeliveryCertainty.POSSIBLY_SENT,
                "worker response timed out after send",
            ) from exc
        except ProtocolError as exc:
            delivery = (
                DeliveryCertainty.POSSIBLY_SENT
                if send_started and exc.code is ProtocolErrorCode.IPC_CONNECTION_LOST
                else DeliveryCertainty.NOT_SENT
            )
            raise WorkerDispatchError(exc.code, delivery, str(exc)) from exc
        finally:
            with self._pending_lock:
                self._pending.pop(envelope.message_id, None)
        if isinstance(item, BaseException):
            raise WorkerDispatchError(
                ProtocolErrorCode.WORKER_CRASHED,
                DeliveryCertainty.POSSIBLY_SENT,
                "worker connection ended after possible send",
            ) from item
        return item

    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult:
        if not self.capabilities.can_submit_orders:
            raise WorkerDispatchError(
                ProtocolErrorCode.WORKER_CAPABILITY_DENIED,
                DeliveryCertainty.NOT_SENT,
                "worker capabilities deny financial submission",
            )
        envelope = Envelope(
            protocol_version=PROTOCOL_VERSION,
            message_id=command.message_id,
            correlation_id=command.correlation_id,
            causation_id=None,
            source=EndpointRole.CORE,
            target=self._worker_role,
            message_type=MessageType.ORDER_SUBMIT,
            created_at_utc=datetime.now(UTC),
            deadline_at=command.deadline_at,
            payload=command.to_payload(),
        )
        response = self._request(envelope, self._response_timeout)
        result = parse_order_response(response)
        if result.correlation_id != command.correlation_id:
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                DeliveryCertainty.POSSIBLY_SENT,
                "order response correlation_id does not match command",
            )
        if result.causation_id != command.message_id:
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                DeliveryCertainty.POSSIBLY_SENT,
                "order response causation_id does not match command",
            )
        self._event_sink.emit(
            "order_response_received",
            message_id=result.response_message_id,
            correlation_id=result.correlation_id,
            outcome=result.outcome.value,
            reason_code=result.reason_code,
        )
        return result

    def receive_order_event(self, timeout: float) -> BrokerOrderEvent | None:
        if timeout <= 0:
            raise ValueError("event receive timeout must be positive")
        try:
            envelope = self._order_events.get(timeout=timeout)
        except queue.Empty:
            return None
        try:
            event = parse_order_event(envelope)
        except ProtocolError as exc:
            self._fail(exc)
            raise
        self._event_sink.emit(
            "broker_event_received_from_ipc",
            message_id=envelope.message_id,
            correlation_id=envelope.correlation_id,
        )
        return event

    @property
    def pending_order_event_count(self) -> int:
        """Bounded queue depth used only by the Core's safe-shutdown drain."""

        return self._order_events.qsize()

    def query_order_status(
        self,
        query: OrderStatusQuery,
        *,
        timeout: float | None = None,
    ) -> OrderStatusResult:
        if not self.capabilities.supports_order_status_query:
            raise StatusQueryError(
                ProtocolErrorCode.RECONCILIATION_UNAVAILABLE,
                "worker does not support order status queries",
            )
        query_timeout = timeout or self._response_timeout
        message_id = str(uuid4())
        envelope = Envelope(
            protocol_version=PROTOCOL_VERSION,
            message_id=message_id,
            correlation_id=query.correlation_id,
            causation_id=None,
            source=EndpointRole.CORE,
            target=self._worker_role,
            message_type=MessageType.ORDER_STATUS_REQUEST,
            created_at_utc=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(seconds=query_timeout),
            payload=query.to_payload(),
        )
        self._event_sink.emit(
            "reconciliation_status_requested",
            message_id=message_id,
            correlation_id=query.correlation_id,
        )
        try:
            response = self._request(envelope, query_timeout)
        except WorkerDispatchError as exc:
            code = (
                ProtocolErrorCode.RECONCILIATION_QUERY_TIMEOUT
                if exc.code is ProtocolErrorCode.ORDER_DISPATCH_AMBIGUOUS
                else ProtocolErrorCode.RECONCILIATION_UNAVAILABLE
            )
            raise StatusQueryError(code, "order status query failed") from exc
        try:
            result = parse_order_status_response(response)
        except ProtocolError as exc:
            raise StatusQueryError(
                ProtocolErrorCode.RECONCILIATION_INVALID_RESPONSE,
                "order status response is invalid",
            ) from exc
        if result.correlation_id != query.correlation_id or result.causation_id != message_id:
            raise StatusQueryError(
                ProtocolErrorCode.RECONCILIATION_INVALID_RESPONSE,
                "order status response routing does not match query",
            )
        self._event_sink.emit(
            "reconciliation_status_received",
            message_id=result.response_message_id,
            correlation_id=result.correlation_id,
            outcome=result.outcome.value,
        )
        return result

    def broker_capabilities(self) -> BrokerCapabilities:
        response = self._read_only_request(MessageType.BROKER_CAPABILITIES_REQUEST, {})
        return parse_broker_capabilities_response(response)

    def quote_digit_contract(
        self,
        *,
        product: str,
        symbol: str,
        amount_minor_units: int,
        currency: str,
        prediction_digit: int | None,
    ) -> Decimal:
        payload: dict[str, object] = {
            "product": product,
            "symbol": symbol,
            "amount_minor_units": amount_minor_units,
            "currency": currency,
        }
        if prediction_digit is not None:
            payload["prediction_digit"] = prediction_digit
        response = self._read_only_request(MessageType.BROKER_QUOTE_REQUEST, payload)
        if response.message_type is not MessageType.BROKER_QUOTE_RESPONSE:
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
                DeliveryCertainty.NOT_SENT,
                "broker quote response is invalid",
            )
        raw_ratio = response.payload.get("net_profit_ratio")
        if not isinstance(raw_ratio, str):
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                DeliveryCertainty.NOT_SENT,
                "broker quote ratio is missing",
            )
        try:
            ratio = Decimal(raw_ratio)
        except InvalidOperation as exc:
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                DeliveryCertainty.NOT_SENT,
                "broker quote ratio is invalid",
            ) from exc
        if not ratio.is_finite() or ratio <= 0:
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                DeliveryCertainty.NOT_SENT,
                "broker quote ratio is not positive",
            )
        return ratio

    def quote_digit_contract_details(
        self,
        *,
        product: str,
        symbol: str,
        amount_minor_units: int,
        currency: str,
        prediction_digit: int | None,
        received_monotonic: float,
    ) -> BrokerProposalQuote:
        payload: dict[str, object] = {
            "product": product,
            "symbol": symbol,
            "amount_minor_units": amount_minor_units,
            "currency": currency,
        }
        if prediction_digit is not None:
            payload["prediction_digit"] = prediction_digit
        response = self._read_only_request(MessageType.BROKER_QUOTE_REQUEST, payload)
        if response.message_type is not MessageType.BROKER_QUOTE_RESPONSE:
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
                DeliveryCertainty.NOT_SENT,
                "broker quote response is invalid",
            )
        return _proposal_quote_from_payload(response.payload, received_monotonic)

    def market_symbols(self) -> tuple[MarketSymbol, ...]:
        response = self._read_only_request(MessageType.MARKET_SYMBOLS_REQUEST, {})
        return parse_market_symbols_response(response)

    def market_contracts(self, symbol: str) -> tuple[ContractMetadata, ...]:
        if not symbol:
            raise ValueError("market symbol is required")
        response = self._read_only_request(
            MessageType.MARKET_CONTRACTS_REQUEST,
            {"broker_symbol": symbol},
        )
        return parse_market_contracts_response(response)

    def subscribe_market_ticks(self, symbol: str) -> MarketTick:
        return self.subscribe_market_tick_snapshot(symbol)[0]

    def subscribe_market_tick_snapshot(
        self, symbol: str
    ) -> tuple[MarketTick, DigitFrequencySnapshot | None]:
        if not symbol:
            raise ValueError("market symbol is required")
        response = self._read_only_request(
            MessageType.MARKET_TICK_SUBSCRIBE,
            {"broker_symbol": symbol},
        )
        raw_frequency = response.payload.get("digit_frequency")
        frequency = (
            DigitFrequencySnapshot.from_payload(raw_frequency)
            if isinstance(raw_frequency, Mapping)
            else None
        )
        return parse_market_tick(response), frequency

    def unsubscribe_market_ticks(self, subscription_id: str) -> bool:
        if not subscription_id:
            raise ValueError("subscription_id is required")
        response = self._read_only_request(
            MessageType.MARKET_TICK_UNSUBSCRIBE,
            {"subscription_id": subscription_id},
        )
        if response.message_type is not MessageType.MARKET_TICK_UNSUBSCRIBED:
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
                DeliveryCertainty.NOT_SENT,
                "market unsubscribe response is invalid",
            )
        cancelled = response.payload.get("cancelled")
        if not isinstance(cancelled, bool):
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                DeliveryCertainty.NOT_SENT,
                "market unsubscribe result is invalid",
            )
        return cancelled

    def market_history(
        self,
        symbol: str,
        *,
        style: str,
        count: int = 100,
        timeframe_seconds: int | None = None,
        end_epoch: int | None = None,
    ) -> tuple[tuple[MarketTick, ...], tuple[MarketCandle, ...]]:
        batch = self.market_history_batch(
            symbol,
            style=style,
            count=count,
            timeframe_seconds=timeframe_seconds,
            end_epoch=end_epoch,
        )
        return batch.ticks, batch.candles

    def market_history_batch(
        self,
        symbol: str,
        *,
        style: str,
        count: int = 100,
        timeframe_seconds: int | None = None,
        end_epoch: int | None = None,
    ) -> MarketHistoryBatch:
        if style not in {"ticks", "candles"}:
            raise ValueError("market history style is invalid")
        payload: dict[str, object] = {
            "broker_symbol": symbol,
            "style": style,
            "count": count,
        }
        if timeframe_seconds is not None:
            payload["timeframe_seconds"] = timeframe_seconds
        if end_epoch is not None:
            if end_epoch <= 0:
                raise ValueError("market history end epoch must be positive")
            payload["end_epoch"] = end_epoch
        response = self._read_only_request(MessageType.MARKET_HISTORY_REQUEST, payload)
        ticks, candles = parse_market_history_response(response)
        if response.causation_id is None:
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                DeliveryCertainty.NOT_SENT,
                "market history response is missing causation_id",
            )
        return MarketHistoryBatch(
            response_message_id=response.message_id,
            correlation_id=response.correlation_id,
            causation_id=response.causation_id,
            ticks=ticks,
            candles=candles,
        )

    def iqoption_binary_payout(self, symbol: str) -> Decimal:
        if self._worker_role is not EndpointRole.IQOPTION_WORKER:
            raise ValueError("IQ Option payout requires IQ Option worker")
        expected = {
            "broker_symbol": symbol,
            "product": "BINARY_OPTION",
            "duration": 1,
            "duration_unit": "m",
        }
        response = self._read_only_request(MessageType.BROKER_QUOTE_REQUEST, expected)
        if response.message_type is not MessageType.BROKER_QUOTE_RESPONSE or any(
            response.payload.get(k) != v for k, v in expected.items()
        ):
            raise ValueError("IQOPTION_PAYOUT_CONTEXT_MISMATCH")
        raw = response.payload.get("payout_return_ratio")
        if not isinstance(raw, str):
            raise ValueError("IQOPTION_PAYOUT_INVALID")
        payout = Decimal(raw)
        if not payout.is_finite() or not 0 < payout <= 1:
            raise ValueError("IQOPTION_PAYOUT_INVALID")
        return payout

    def broker_clock(self) -> BrokerClockSnapshot:
        response = self._read_only_request(MessageType.BROKER_CLOCK_REQUEST, {})
        return parse_broker_clock_response(response)

    def broker_balance(self) -> BrokerAccountBalance:
        response = self._read_only_request(MessageType.BROKER_BALANCE_REQUEST, {})
        return parse_broker_balance_response(response)

    def receive_market_tick(self, timeout: float) -> MarketTick | None:
        item = self.receive_market_tick_snapshot(timeout)
        return None if item is None else item[0]

    def receive_market_tick_snapshot(
        self, timeout: float
    ) -> tuple[MarketTick, DigitFrequencySnapshot | None] | None:
        if timeout <= 0:
            raise ValueError("market event timeout must be positive")
        try:
            envelope = self._market_events.get(timeout=timeout)
        except queue.Empty:
            return None
        raw_frequency = envelope.payload.get("digit_frequency")
        try:
            frequency = (
                None
                if raw_frequency is None
                else DigitFrequencySnapshot.from_payload(raw_frequency)
                if isinstance(raw_frequency, Mapping)
                else None
            )
        except ValueError as exc:
            raise ProtocolError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                "invalid digit frequency snapshot",
            ) from exc
        return parse_market_tick(envelope), frequency

    def _read_only_request(
        self,
        message_type: MessageType,
        payload: dict[str, object],
    ) -> Envelope:
        message_id = str(uuid4())
        correlation_id = str(uuid4())
        envelope = Envelope(
            protocol_version=PROTOCOL_VERSION,
            message_id=message_id,
            correlation_id=correlation_id,
            causation_id=None,
            source=EndpointRole.CORE,
            target=self._worker_role,
            message_type=message_type,
            created_at_utc=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(seconds=self._response_timeout),
            payload=payload,
        )
        response = self._request(envelope, self._response_timeout)
        if response.causation_id != message_id or response.correlation_id != correlation_id:
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                DeliveryCertainty.NOT_SENT,
                "read-only response routing does not match request",
            )
        if response.message_type is MessageType.ERROR:
            raw_code = response.payload.get("reason_code")
            try:
                code = ProtocolErrorCode(str(raw_code))
            except ValueError:
                code = ProtocolErrorCode.IPC_INVALID_ENVELOPE
            raise WorkerDispatchError(
                code,
                DeliveryCertainty.NOT_SENT,
                "worker rejected read-only request",
            )
        return response

    def ping(self, timeout: float | None = None) -> None:
        message_id = str(uuid4())
        envelope = Envelope(
            protocol_version=PROTOCOL_VERSION,
            message_id=message_id,
            correlation_id=str(uuid4()),
            causation_id=None,
            source=EndpointRole.CORE,
            target=self._worker_role,
            message_type=MessageType.PING,
            created_at_utc=datetime.now(UTC),
            deadline_at=None,
            payload={},
        )
        response = self._request(envelope, timeout or self._response_timeout)
        if response.message_type is not MessageType.PONG:
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
                DeliveryCertainty.NOT_SENT,
                "heartbeat received an invalid response",
            )

    def request_health(self) -> str:
        response = self.request_health_snapshot()
        status = response.get("status")
        if not isinstance(status, str):
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                DeliveryCertainty.NOT_SENT,
                "worker health status is invalid",
            )
        return status

    def request_health_snapshot(self) -> dict[str, object]:
        message_id = str(uuid4())
        envelope = Envelope(
            protocol_version=PROTOCOL_VERSION,
            message_id=message_id,
            correlation_id=str(uuid4()),
            causation_id=None,
            source=EndpointRole.CORE,
            target=self._worker_role,
            message_type=MessageType.WORKER_HEALTH_REQUEST,
            created_at_utc=datetime.now(UTC),
            deadline_at=None,
            payload={},
        )
        response = self._request(envelope, self._response_timeout)
        if response.message_type is not MessageType.WORKER_HEALTH_RESPONSE:
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
                DeliveryCertainty.NOT_SENT,
                "worker health response has invalid type",
            )
        status = response.payload.get("status")
        if not isinstance(status, str):
            raise WorkerDispatchError(
                ProtocolErrorCode.IPC_INVALID_ENVELOPE,
                DeliveryCertainty.NOT_SENT,
                "worker health status is invalid",
            )
        return dict(response.payload)

    def shutdown(self, timeout: float) -> bool:
        if not self.is_ready:
            self.close()
            return False
        message_id = str(uuid4())
        envelope = Envelope(
            protocol_version=PROTOCOL_VERSION,
            message_id=message_id,
            correlation_id=str(uuid4()),
            causation_id=None,
            source=EndpointRole.CORE,
            target=self._worker_role,
            message_type=MessageType.SHUTDOWN,
            created_at_utc=datetime.now(UTC),
            deadline_at=None,
            payload={},
        )
        try:
            response = self._request(envelope, timeout)
            return response.message_type is MessageType.SHUTDOWN_ACK
        except WorkerDispatchError:
            return False
        finally:
            self.close()

    def receive_event(self, timeout: float = 0.0) -> Envelope:
        return self._order_events.get(timeout=timeout)

    def close(self) -> None:
        self._closing.set()
        self._ready = False
        self._transport.close()
        if threading.current_thread() is not self._reader:
            self._reader.join(timeout=1.0)
