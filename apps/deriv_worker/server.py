from __future__ import annotations

import os
import socket
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from apps.deriv_worker.fake_transport import FakeDerivScenario
from apps.deriv_worker.order_session import DerivOrderSession
from apps.deriv_worker.public_session import PublicDerivSession
from apps.deriv_worker.reconciliation import DerivReconciliationHandler
from apps.deriv_worker.schema import DerivErrorCategory, DerivWorkerError
from apps.deriv_worker.tick_stream import DerivTickStream
from packages.domain.market import BrokerAccountBalance
from packages.domain.models import WorkerOutcome
from packages.protocol.envelope import EndpointRole, Envelope, MessageType
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.protocol.messages import (
    WorkerCapabilities,
    parse_order_status_request,
    parse_order_submit,
)
from packages.protocol.transport import FramedSocket


class DerivWorkerServer:
    """Deriv IPC worker adapter for market data and demo order execution."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        protocol_version: int,
        session: PublicDerivSession,
        scenario: FakeDerivScenario = FakeDerivScenario.NORMAL,
        connect_timeout: float = 3.0,
        stream_poll_seconds: float = 0.05,
        suspension_gap_seconds: float = 30.0,
        order_session: DerivOrderSession | None = None,
        reconciliation_handler: DerivReconciliationHandler | None = None,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("Deriv worker IPC must use IPv4 loopback")
        self._host = host
        self._port = port
        self._protocol_version = protocol_version
        self._session = session
        self._scenario = scenario
        capabilities = session.capabilities
        self._order_session: DerivOrderSession | None
        self._reconciliation_handler: DerivReconciliationHandler | None
        can_submit_orders = order_session is not None and getattr(
            order_session, "trading_authenticated", False
        )
        if can_submit_orders:
            assert order_session is not None
            self._capabilities = WorkerCapabilities(
                broker="DERIV",
                account_modes=(order_session.account_type,),
                products=(
                    "DIGITAL_OPTION",
                    "DIGITDIFF",
                    "DIGITOVER",
                    "DIGITUNDER",
                    "DIGITEVEN",
                    "DIGITODD",
                    "OPTIONS",
                    "MARKET_DATA",
                ),
                supports_reconciliation=True,
                supports_quotes=True,
                supports_order_status_query=True,
                supports_order_events=True,
                worker_version="0.4.0",
                can_submit_orders=True,
                supports_market_data=True,
                connection_mode=order_session.account_type.upper(),
            )
            self._order_session = order_session
            self._reconciliation_handler = reconciliation_handler or (
                DerivReconciliationHandler(session.transport, order_session)
            )
        else:
            self._capabilities = WorkerCapabilities(
                broker="DERIV",
                account_modes=(capabilities.connection_mode.value,),
                products=("MARKET_DATA",),
                supports_reconciliation=False,
                supports_quotes=True,
                supports_order_status_query=False,
                supports_order_events=False,
                worker_version="0.4.0",
                can_submit_orders=False,
                supports_market_data=True,
                connection_mode=capabilities.connection_mode.value,
            )
            self._order_session = None
            self._reconciliation_handler = None
        self._connect_timeout = connect_timeout
        self._stream_poll_seconds = stream_poll_seconds
        self._suspension_gap_seconds = suspension_gap_seconds
        self._send_lock = threading.Lock()
        # The authenticated Deriv transport has a single receive stream. IPC
        # requests (history, balance, orders) and the live market pump must not
        # consume websocket frames concurrently or one path can steal the
        # other's response and force an otherwise healthy session to close.
        self._session_io_lock = threading.RLock()
        self._digit_tick_streams: dict[str, DerivTickStream] = {}
        self._digit_stream_subscription_symbols: dict[str, str] = {}
        self._pump_stop = threading.Event()
        self._pump_thread: threading.Thread | None = None

    def run(self) -> int:
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(self._connect_timeout)
        connection.connect((self._host, self._port))
        connection.settimeout(None)
        transport = FramedSocket(connection)
        try:
            self._session.connect()
            if not self._handshake(transport):
                return 2
            if self._scenario is FakeDerivScenario.CRASH_AFTER_HANDSHAKE:
                os._exit(76)
            self._start_market_pump(transport)
            while True:
                request = transport.receive()
                self._validate_routing(request)
                if request.message_type is MessageType.SHUTDOWN:
                    self._reply(transport, request, MessageType.SHUTDOWN_ACK, {})
                    return 0
                self._handle(transport, request)
        except DerivWorkerError:
            return 4
        except ProtocolError:
            return 3
        finally:
            self._stop_market_pump()
            self._session.close()
            transport.close()

    def _handshake(self, transport: FramedSocket) -> bool:
        hello = transport.receive()
        if (
            hello.message_type is not MessageType.HELLO
            or hello.source is not EndpointRole.CORE
            or hello.target is not EndpointRole.DERIV_WORKER
        ):
            raise ProtocolError(
                ProtocolErrorCode.IPC_ROLE_MISMATCH,
                "first Deriv worker message must be CORE HELLO",
            )
        self._reply(
            transport,
            hello,
            MessageType.HELLO_ACK,
            self._capabilities.to_payload(),
        )
        return hello.protocol_version == self._protocol_version

    def _validate_routing(self, request: Envelope) -> None:
        if (
            request.protocol_version != self._protocol_version
            or request.source is not EndpointRole.CORE
            or request.target is not EndpointRole.DERIV_WORKER
        ):
            raise ProtocolError(
                ProtocolErrorCode.IPC_ROLE_MISMATCH,
                "invalid Core to Deriv worker routing",
            )

    def _handle(self, transport: FramedSocket, request: Envelope) -> None:
        try:
            with self._session_io_lock:
                message_type, payload = self._dispatch_read_only(request)
        except (DerivWorkerError, ValueError) as exc:
            reason = (
                exc.reason_code
                if isinstance(exc, DerivWorkerError)
                else ProtocolErrorCode.MARKET_DATA_INVALID.value
            )
            self._reply(
                transport,
                request,
                MessageType.ERROR,
                {"reason_code": reason},
            )
            return
        self._reply(transport, request, message_type, payload)

    def _dispatch_read_only(self, request: Envelope) -> tuple[MessageType, dict[str, object]]:
        message_type = request.message_type
        if message_type is MessageType.PING:
            return MessageType.PONG, {}
        if message_type is MessageType.WORKER_HEALTH_REQUEST:
            manager = self._session.subscriptions
            return MessageType.WORKER_HEALTH_RESPONSE, {
                "status": self._session.health.value,
                "messages_received": self._session.messages_received,
                "reconnect_count": self._session.reconnect_count,
                "schema_errors": self._session.schema_errors,
                "ticks_received": manager.ticks_received,
                "ticks_dropped": manager.ticks_dropped,
                "duplicate_ticks": manager.duplicates,
                "late_ticks": manager.late_ticks,
            }
        if message_type is MessageType.ORDER_SUBMIT:
            if not self._capabilities.can_submit_orders or self._order_session is None:
                return MessageType.ERROR, {
                    "reason_code": ProtocolErrorCode.WORKER_CAPABILITY_DENIED.value
                }
            command = parse_order_submit(request)
            result = self._order_session.submit_order(command)
            if result.outcome is WorkerOutcome.ACCEPTED:
                return MessageType.ORDER_ACCEPTED, {
                    "order_id": command.order_id,
                    "broker_order_id": result.broker_order_id,
                    "reason_code": None,
                }
            if result.outcome is WorkerOutcome.REJECTED:
                return MessageType.ORDER_REJECTED, {
                    "order_id": command.order_id,
                    "broker_order_id": None,
                    "reason_code": result.reason_code or "ORDER_REJECTED",
                }
            return MessageType.ORDER_STATUS_UNKNOWN, {
                "order_id": command.order_id,
                "broker_order_id": None,
                "reason_code": result.reason_code or "TIMEOUT_AFTER_POSSIBLE_SEND",
            }
        if message_type is MessageType.ORDER_STATUS_REQUEST:
            if (
                not self._capabilities.supports_order_status_query
                or self._reconciliation_handler is None
            ):
                return MessageType.ERROR, {
                    "reason_code": ProtocolErrorCode.RECONCILIATION_UNAVAILABLE.value
                }
            query = parse_order_status_request(request)
            status_result = self._reconciliation_handler.query_order_status(
                query, causation_id=request.message_id
            )
            evidence_payload = (
                status_result.evidence.to_payload() if status_result.evidence is not None else None
            )
            return MessageType.ORDER_STATUS_RESPONSE, {
                "query_outcome": status_result.outcome.value,
                "reason_code": status_result.reason_code,
                "evidence": evidence_payload,
            }
        if message_type is MessageType.BROKER_CAPABILITIES_REQUEST:
            return MessageType.BROKER_CAPABILITIES_RESPONSE, {
                **self._session.capabilities.to_payload()
            }
        if message_type is MessageType.MARKET_SYMBOLS_REQUEST:
            return MessageType.MARKET_SYMBOLS_RESPONSE, {
                "symbols": [item.to_payload() for item in self._session.active_symbols()]
            }
        if message_type is MessageType.MARKET_CONTRACTS_REQUEST:
            symbol = self._required_str(request.payload, "broker_symbol")
            return MessageType.MARKET_CONTRACTS_RESPONSE, {
                "contracts": [item.to_payload() for item in self._session.contracts_for(symbol)]
            }
        if message_type is MessageType.MARKET_TICK_SUBSCRIBE:
            symbol = self._required_str(request.payload, "broker_symbol")
            digit_stream = self._digit_tick_streams.setdefault(
                symbol,
                DerivTickStream(self._session.transport),
            )
            digit_stream.activate_symbol(symbol)
            tick = self._session.subscribe_ticks(
                symbol,
                correlation_id=request.correlation_id,
            )
            try:
                for historical_tick in self._session.tick_history(symbol, count=500):
                    digit_stream.ingest_market_tick(historical_tick)
            except (DerivWorkerError, ValueError):
                pass
            frequency = digit_stream.ingest_market_tick(tick)
            self._digit_stream_subscription_symbols[tick.subscription_id] = symbol
            return MessageType.MARKET_TICK_SUBSCRIBED, {
                "digit_frequency": frequency.to_payload(),
                "tick": tick.to_payload(),
            }
        if message_type is MessageType.MARKET_TICK_UNSUBSCRIBE:
            subscription_id = self._required_str(request.payload, "subscription_id")
            unsubscribed_symbol = self._digit_stream_subscription_symbols.get(subscription_id)
            if unsubscribed_symbol is not None:
                self._digit_stream_subscription_symbols.pop(subscription_id)
            if (
                unsubscribed_symbol is not None
                and unsubscribed_symbol not in self._digit_stream_subscription_symbols.values()
            ):
                self._digit_tick_streams.pop(unsubscribed_symbol, None)
            return MessageType.MARKET_TICK_UNSUBSCRIBED, {
                "cancelled": self._session.unsubscribe(subscription_id)
            }
        if message_type is MessageType.MARKET_HISTORY_REQUEST:
            return MessageType.MARKET_HISTORY_RESPONSE, self._history_payload(request.payload)
        if message_type is MessageType.BROKER_CLOCK_REQUEST:
            return MessageType.BROKER_CLOCK_RESPONSE, self._session.clock().to_payload()
        if message_type is MessageType.BROKER_BALANCE_REQUEST:
            getter = getattr(self._session, "account_balance", None)
            if not callable(getter):
                raise DerivWorkerError(
                    DerivErrorCategory.AUTH_FAILED,
                    "DERIV_DEMO_AUTH_REQUIRED",
                )
            balance = cast(Callable[[], BrokerAccountBalance], getter)()
            return MessageType.BROKER_BALANCE_RESPONSE, balance.to_payload()
        raise ProtocolError(
            ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
            "message type is not accepted by Deriv worker",
        )

    def _history_payload(self, payload: Mapping[str, object]) -> dict[str, object]:
        symbol = self._required_str(payload, "broker_symbol")
        style = self._required_str(payload, "style")
        count = payload.get("count", 100)
        if isinstance(count, bool) or not isinstance(count, int):
            raise ValueError("history count must be an integer")
        # One IPC frame is capped at 64 KiB. A 500-tick response exceeds that
        # bound, so callers must page history instead of weakening the protocol.
        if not 1 <= count <= 100:
            raise ValueError("history IPC page size is outside bounds")
        if style == "ticks":
            end_epoch = payload.get("end_epoch")
            if end_epoch is not None and (
                isinstance(end_epoch, bool) or not isinstance(end_epoch, int) or end_epoch <= 0
            ):
                raise ValueError("tick history end epoch must be a positive integer")
            return {
                "ticks": [
                    item.to_payload()
                    for item in self._session.tick_history(
                        symbol,
                        count=count,
                        end_epoch=end_epoch,
                    )
                ],
                "candles": [],
            }
        if style == "candles":
            timeframe = payload.get("timeframe_seconds")
            if isinstance(timeframe, bool) or not isinstance(timeframe, int):
                raise ValueError("candle timeframe must be an integer")
            end_epoch = payload.get("end_epoch")
            if end_epoch is not None and (
                isinstance(end_epoch, bool) or not isinstance(end_epoch, int) or end_epoch <= 0
            ):
                raise ValueError("candle history end epoch must be a positive integer")
            return {
                "ticks": [],
                "candles": [
                    item.to_payload()
                    for item in self._session.candle_history(
                        symbol,
                        timeframe,
                        count=count,
                        end_epoch=end_epoch,
                    )
                ],
            }
        raise ValueError("unknown market history style")

    @staticmethod
    def _required_str(payload: Mapping[str, object], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        return value

    def _reply(
        self,
        transport: FramedSocket,
        request: Envelope,
        message_type: MessageType,
        payload: dict[str, object],
    ) -> None:
        self._send(
            transport,
            Envelope(
                protocol_version=self._protocol_version,
                message_id=str(uuid4()),
                correlation_id=request.correlation_id,
                causation_id=request.message_id,
                source=EndpointRole.DERIV_WORKER,
                target=EndpointRole.CORE,
                message_type=message_type,
                created_at_utc=datetime.now(UTC),
                deadline_at=None,
                payload=payload,
            ),
        )

    def _start_market_pump(self, transport: FramedSocket) -> None:
        self._pump_stop.clear()
        self._pump_thread = threading.Thread(
            target=self._market_pump_loop,
            args=(transport,),
            name="deriv-market-pump",
            daemon=True,
        )
        self._pump_thread.start()

    def _stop_market_pump(self) -> None:
        self._pump_stop.set()
        pump = self._pump_thread
        self._pump_thread = None
        if pump is not None and pump is not threading.current_thread():
            pump.join(timeout=1.0)

    def _market_pump_loop(self, transport: FramedSocket) -> None:
        while not self._pump_stop.wait(self._stream_poll_seconds):
            with self._session_io_lock:
                self._session.detect_suspension(max_gap_seconds=self._suspension_gap_seconds)
                if self._order_session is not None:
                    try:
                        self._order_session.drain_contract_events(timeout=0.0)
                        order_event = self._order_session.next_queued_event(timeout=0.0)
                        while order_event is not None:
                            order_envelope = Envelope(
                                protocol_version=self._protocol_version,
                                message_id=str(uuid4()),
                                correlation_id=order_event.correlation_id,
                                causation_id=None,
                                source=EndpointRole.DERIV_WORKER,
                                target=EndpointRole.CORE,
                                message_type=MessageType.ORDER_EVENT,
                                created_at_utc=datetime.now(UTC),
                                deadline_at=None,
                                payload=order_event.to_payload(),
                            )
                            self._send(transport, order_envelope)
                            order_event = self._order_session.next_queued_event(timeout=0.0)
                    except Exception:
                        pass
                try:
                    tick = self._session.next_queued_tick(timeout=0.0)
                    if tick is None:
                        self._session.drain_stream_once(timeout=self._stream_poll_seconds)
                        tick = self._session.next_queued_tick(timeout=0.0)
                except DerivWorkerError:
                    return
            if tick is None:
                continue
            digit_stream = self._digit_tick_streams.get(tick.broker_symbol)
            if digit_stream is None:
                continue
            frequency = digit_stream.ingest_market_tick(tick)
            event = Envelope(
                protocol_version=self._protocol_version,
                message_id=str(uuid4()),
                correlation_id=self._session.event_correlation_id(tick),
                causation_id=None,
                source=EndpointRole.DERIV_WORKER,
                target=EndpointRole.CORE,
                message_type=MessageType.MARKET_TICK_EVENT,
                created_at_utc=datetime.now(UTC),
                deadline_at=None,
                payload={
                    "digit_frequency": frequency.to_payload(),
                    "tick": tick.to_payload(),
                },
            )
            try:
                self._send(transport, event)
            except (ProtocolError, OSError):
                return

    def _send(self, transport: FramedSocket, envelope: Envelope) -> None:
        with self._send_lock:
            transport.send(envelope)
