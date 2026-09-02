from __future__ import annotations

import socket
import threading
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from apps.iqoption_worker.order_session import IQOptionOrderSession
from apps.iqoption_worker.reconciliation import IQOptionReconciliationHandler
from packages.brokers.iqoption.community_read_only import (
    IQOptionCommunityReadOnlySession,
    IQOptionExternalError,
)
from packages.domain.models import WorkerOutcome
from packages.protocol.envelope import EndpointRole, Envelope, MessageType
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.protocol.messages import (
    WorkerCapabilities,
    parse_order_status_request,
    parse_order_submit,
)
from packages.protocol.transport import FramedSocket


class IQOptionReadOnlyWorkerServer:
    """Authenticated IQ Option worker; financial capability is Practice-only."""

    def __init__(
        self,
        host: str,
        port: int,
        protocol_version: int,
        session: IQOptionCommunityReadOnlySession,
        *,
        connection_mode: str,
    ) -> None:
        self._host = host
        self._port = port
        self._protocol_version = protocol_version
        self._session = session
        self._connection_mode = connection_mode
        self._connect_attempted = False
        self._stopping = False
        is_demo = "DEMO" in connection_mode or "PRACTICE" in connection_mode
        self._order_session = IQOptionOrderSession(session, practice_mode=True) if is_demo else None
        self._reconciliation = (
            IQOptionReconciliationHandler(session, self._order_session)
            if self._order_session is not None
            else None
        )
        self._pump_stop = threading.Event()
        self._pump_thread: threading.Thread | None = None
        self._capabilities = WorkerCapabilities(
            broker="IQOPTION",
            account_modes=("PRACTICE", "REAL"),
            products=("ACCOUNT_READ_ONLY", "BINARY_OPTION", "OPTIONS"),
            supports_reconciliation=is_demo,
            supports_quotes=is_demo,
            supports_order_status_query=is_demo,
            worker_version="1.0.0-community",
            supports_order_events=is_demo,
            can_submit_orders=is_demo,
            supports_market_data=is_demo,
            connection_mode=("DEMO_AUTH_FINANCIAL" if is_demo else "REAL_AUTH_READ_ONLY"),
        )

    def run(self) -> int:
        # The Core supervisor owns the loopback listener.  Workers connect to
        # that listener, matching the IPC direction used by every other
        # Trading Lab worker.  Binding/listening here would leave both sides
        # waiting for the other and surface as IPC_HANDSHAKE_TIMEOUT.
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(10.0)
        try:
            connection.connect((self._host, self._port))
            connection.settimeout(None)
        except (OSError, TimeoutError):
            connection.close()
            return 2

        framed = FramedSocket(connection)
        try:
            if not self._handshake(framed):
                return 3
            self._start_event_pump(framed)
            while not self._stopping:
                request = framed.receive()
                self._validate_routing(request)
                message_type, payload = self._dispatch(request)
                framed.send(self._response(request, message_type, payload))
        except (ConnectionError, EOFError, OSError, ProtocolError):
            return 1
        finally:
            self._stop_event_pump()
            self._session.close()
            framed.close()
        return 0

    def _handshake(self, framed: FramedSocket) -> bool:
        request = framed.receive()
        if (
            request.message_type is not MessageType.HELLO
            or request.target is not EndpointRole.IQOPTION_WORKER
        ):
            return False
        framed.send(self._response(request, MessageType.HELLO_ACK, self._capabilities.to_payload()))
        return True

    def _dispatch(self, request: Envelope) -> tuple[MessageType, dict[str, Any]]:
        try:
            if request.message_type is MessageType.PING:
                if self._connect_attempted and not self._session.is_connected:
                    # Recover the transport in place with the memory-only SSID.
                    # This path cannot perform HTTP login, so heartbeat cannot
                    # create an authentication storm behind Core's persistent
                    # connection admission controller.
                    self._session.reconnect(timeout=8.0)
                return MessageType.PONG, {}
            if request.message_type is MessageType.BROKER_BALANCE_REQUEST:
                self._ensure_connected()
                return MessageType.BROKER_BALANCE_RESPONSE, self._session.get_balance().to_payload()
            if request.message_type is MessageType.BROKER_CLOCK_REQUEST:
                self._ensure_connected()
                return MessageType.BROKER_CLOCK_RESPONSE, self._session.get_clock().to_payload()
            if request.message_type is MessageType.BROKER_CAPABILITIES_REQUEST:
                return MessageType.BROKER_CAPABILITIES_RESPONSE, self._capabilities.to_payload()
            if request.message_type is MessageType.MARKET_HISTORY_REQUEST:
                self._ensure_connected()
                if self._order_session is None:
                    return self._error_payload("WORKER_CAPABILITY_DENIED")
                symbol = request.payload.get("broker_symbol")
                style = request.payload.get("style")
                count = request.payload.get("count")
                timeframe = request.payload.get("timeframe_seconds")
                end_epoch = request.payload.get("end_epoch")
                if (
                    not isinstance(symbol, str)
                    or style != "candles"
                    or isinstance(count, bool)
                    or not isinstance(count, int)
                    or isinstance(timeframe, bool)
                    or not isinstance(timeframe, int)
                    or (
                        end_epoch is not None
                        and (isinstance(end_epoch, bool) or not isinstance(end_epoch, int))
                    )
                ):
                    return self._error_payload("IPC_INVALID_ENVELOPE")
                candles = self._session.get_candles(
                    symbol,
                    timeframe_seconds=timeframe,
                    count=count,
                    end_epoch=end_epoch,
                )
                return MessageType.MARKET_HISTORY_RESPONSE, {
                    "ticks": [],
                    "candles": [candle.to_payload() for candle in candles],
                }
            if request.message_type is MessageType.ORDER_SUBMIT:
                order_session = self._order_session
                if order_session is None:
                    return self._error_payload("WORKER_CAPABILITY_DENIED")
                self._ensure_connected()
                command = parse_order_submit(request)
                result = order_session.submit_order(command)
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
            if request.message_type is MessageType.ORDER_STATUS_REQUEST:
                self._ensure_connected()
                reconciliation = self._reconciliation
                if reconciliation is None:
                    return self._error_payload("WORKER_CAPABILITY_DENIED")
                query = parse_order_status_request(request)
                status_result = reconciliation.query_order_status(
                    query,
                    causation_id=request.message_id,
                )
                return MessageType.ORDER_STATUS_RESPONSE, {
                    "query_outcome": status_result.outcome.value,
                    "evidence": (
                        None
                        if status_result.evidence is None
                        else status_result.evidence.to_payload()
                    ),
                    "reason_code": status_result.reason_code,
                }
            if request.message_type is MessageType.SHUTDOWN:
                self._stopping = True
                return MessageType.SHUTDOWN_ACK, {}
        except IQOptionExternalError as exc:
            return self._error_payload(exc.reason_code)
        return self._error_payload("IPC_UNKNOWN_MESSAGE_TYPE")

    def _start_event_pump(self, framed: FramedSocket) -> None:
        order_session = self._order_session
        if order_session is None:
            return
        self._pump_stop.clear()

        def pump() -> None:
            while not self._pump_stop.is_set():
                try:
                    order_session.drain_contract_events(timeout=0.05)
                    while True:
                        event = order_session.next_queued_event(timeout=0.0)
                        if event is None:
                            break
                        framed.send(
                            Envelope(
                                protocol_version=self._protocol_version,
                                message_id=str(uuid4()),
                                correlation_id=event.correlation_id,
                                causation_id=None,
                                source=EndpointRole.IQOPTION_WORKER,
                                target=EndpointRole.CORE,
                                message_type=MessageType.ORDER_EVENT,
                                created_at_utc=datetime.now(UTC),
                                deadline_at=None,
                                payload=event.to_payload(),
                            )
                        )
                except Exception:
                    return
                time.sleep(0.01)

        self._pump_thread = threading.Thread(
            target=pump,
            name="iqoption-external-event-pump",
            daemon=True,
        )
        self._pump_thread.start()

    def _stop_event_pump(self) -> None:
        self._pump_stop.set()
        thread = self._pump_thread
        self._pump_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _ensure_connected(self) -> None:
        if self._session.is_connected:
            return
        if self._connect_attempted:
            # After the first session start, every in-worker recovery is SSID
            # only.  A market-data request racing the heartbeat cannot bypass
            # the bounded reconnect path and trigger a fresh HTTP login.
            self._session.reconnect(timeout=8.0)
            return
        self._connect_attempted = True
        self._session.connect()

    @staticmethod
    def _error_payload(reason_code: str) -> tuple[MessageType, dict[str, Any]]:
        try:
            normalized = ProtocolErrorCode(reason_code).value
        except ValueError:
            normalized = ProtocolErrorCode.IPC_INVALID_ENVELOPE.value
        return MessageType.ERROR, {"reason_code": normalized}

    def _validate_routing(self, request: Envelope) -> None:
        if (
            request.protocol_version != self._protocol_version
            or request.source is not EndpointRole.CORE
            or request.target is not EndpointRole.IQOPTION_WORKER
        ):
            raise ProtocolError(
                ProtocolErrorCode.IPC_ROLE_MISMATCH,
                "invalid Core to IQ Option worker routing",
            )

    def _response(
        self,
        request: Envelope,
        message_type: MessageType,
        payload: dict[str, Any],
    ) -> Envelope:
        return Envelope(
            protocol_version=self._protocol_version,
            message_id=str(uuid4()),
            correlation_id=request.correlation_id,
            causation_id=request.message_id,
            source=EndpointRole.IQOPTION_WORKER,
            target=request.source,
            message_type=message_type,
            created_at_utc=datetime.now(UTC),
            deadline_at=None,
            payload=payload,
        )


__all__ = ["IQOptionReadOnlyWorkerServer"]
