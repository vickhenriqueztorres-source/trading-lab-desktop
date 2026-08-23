from __future__ import annotations

import logging
import socket
import threading
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from apps.iqoption_worker.order_session import IQOptionOrderSession
from apps.iqoption_worker.reconciliation import IQOptionReconciliationHandler
from apps.iqoption_worker.schema import IQOptionWorkerError
from packages.brokers.iqoption.fake_transport import FakeIQOptionScenario
from packages.brokers.iqoption.session import IQOptionPracticeSession
from packages.domain.models import WorkerOutcome
from packages.protocol.envelope import EndpointRole, Envelope, MessageType
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.protocol.messages import (
    WorkerCapabilities,
    parse_order_status_request,
    parse_order_submit,
)
from packages.protocol.transport import FramedSocket

logger = logging.getLogger("iqoption_worker.server")


class IQOptionWorkerServer:
    """IPC v1 loopback server for the isolated IQ Option practice worker."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        protocol_version: int = 1,
        session: IQOptionPracticeSession,
        order_session: IQOptionOrderSession | None = None,
        reconciliation_handler: IQOptionReconciliationHandler | None = None,
        scenario: FakeIQOptionScenario = FakeIQOptionScenario.NORMAL,
    ) -> None:
        self._host = host
        self._port = port
        self._protocol_version = protocol_version
        self._session = session
        self._scenario = scenario
        self._order_session = order_session or IQOptionOrderSession(
            session.transport, practice_mode=True
        )
        self._reconciliation_handler = reconciliation_handler or IQOptionReconciliationHandler(
            session.transport,
            self._order_session,
        )
        self._capabilities = WorkerCapabilities(
            broker="IQOPTION",
            account_modes=("practice",),
            products=("BINARY_OPTION", "DIGITAL_OPTION", "OPTIONS"),
            supports_reconciliation=True,
            supports_quotes=True,
            supports_order_status_query=True,
            supports_order_events=True,
            worker_version="0.4.0",
            can_submit_orders=True,
            supports_market_data=True,
            connection_mode="PRACTICE",
        )
        self._stopping = threading.Event()
        self._pump_stop = threading.Event()
        self._pump_thread: threading.Thread | None = None

    def run(self) -> int:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self._host, self._port))
        server_socket.listen(1)
        server_socket.settimeout(5.0)

        try:
            self._session.connect()
        except IQOptionWorkerError:
            server_socket.close()
            return 2

        try:
            conn, _ = server_socket.accept()
        except TimeoutError:
            server_socket.close()
            return 1

        framed = FramedSocket(conn)
        try:
            if not self._handshake(framed):
                return 3
            self._start_event_pump(framed)
            while not self._stopping.is_set():
                request = framed.receive()
                self._validate_routing(request)
                self._handle(framed, request)
        except (ConnectionError, EOFError, OSError, ProtocolError):
            pass
        finally:
            self._stop_event_pump()
            self._session.close()
            framed.close()
            server_socket.close()
        return 0

    def _handshake(self, framed: FramedSocket) -> bool:
        request = framed.receive()
        if request.message_type is not MessageType.HELLO:
            return False
        if request.target is not EndpointRole.IQOPTION_WORKER:
            return False
        reply = Envelope(
            protocol_version=self._protocol_version,
            message_id=str(uuid4()),
            correlation_id=request.correlation_id,
            causation_id=request.message_id,
            source=EndpointRole.IQOPTION_WORKER,
            target=request.source,
            message_type=MessageType.HELLO_ACK,
            created_at_utc=datetime.now(UTC),
            deadline_at=None,
            payload=self._capabilities.to_payload(),
        )
        framed.send(reply)
        return True

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

    def _handle(self, framed: FramedSocket, request: Envelope) -> None:
        msg_type, payload = self._dispatch(request)
        reply = Envelope(
            protocol_version=self._protocol_version,
            message_id=str(uuid4()),
            correlation_id=request.correlation_id,
            causation_id=request.message_id,
            source=EndpointRole.IQOPTION_WORKER,
            target=request.source,
            message_type=msg_type,
            created_at_utc=datetime.now(UTC),
            deadline_at=None,
            payload=payload,
        )
        framed.send(reply)
        if msg_type is MessageType.SHUTDOWN_ACK:
            self._stopping.set()

    def _dispatch(self, request: Envelope) -> tuple[MessageType, dict[str, Any]]:
        if request.message_type is MessageType.ORDER_SUBMIT:
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

        if request.message_type is MessageType.ORDER_STATUS_REQUEST:
            status_query = parse_order_status_request(request)
            status_result = self._reconciliation_handler.query_order_status(
                status_query, causation_id=request.message_id
            )
            evidence_payload = (
                status_result.evidence.to_payload() if status_result.evidence is not None else None
            )
            return MessageType.ORDER_STATUS_RESPONSE, {
                "query_outcome": status_result.outcome.value,
                "evidence": evidence_payload,
                "reason_code": status_result.reason_code,
            }

        if request.message_type is MessageType.BROKER_BALANCE_REQUEST:
            balance = self._session.get_balance()
            return MessageType.BROKER_BALANCE_RESPONSE, balance.to_payload()

        if request.message_type is MessageType.BROKER_CLOCK_REQUEST:
            clock = self._session.get_clock()
            return MessageType.BROKER_CLOCK_RESPONSE, clock.to_payload()

        if request.message_type is MessageType.BROKER_CAPABILITIES_REQUEST:
            return MessageType.BROKER_CAPABILITIES_RESPONSE, self._capabilities.to_payload()

        if request.message_type is MessageType.SHUTDOWN:
            return MessageType.SHUTDOWN_ACK, {}

        raise ProtocolError(
            ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
            f"unsupported IQ Option message type: {request.message_type.value}",
        )

    def _start_event_pump(self, framed: FramedSocket) -> None:
        self._pump_stop.clear()

        def _pump_loop() -> None:
            while not self._pump_stop.is_set():
                try:
                    self._order_session.drain_contract_events(timeout=0.05)
                    while True:
                        event = self._order_session.next_queued_event(timeout=0.0)
                        if event is None:
                            break
                        envelope = Envelope(
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
                        framed.send(envelope)
                except Exception:
                    break
                time.sleep(0.01)

        self._pump_thread = threading.Thread(
            target=_pump_loop,
            name="iqoption-event-pump",
            daemon=True,
        )
        self._pump_thread.start()

    def _stop_event_pump(self) -> None:
        self._pump_stop.set()
        if self._pump_thread is not None:
            self._pump_thread.join(timeout=1.0)
            self._pump_thread = None
