from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from apps.simulated_worker.broker_store import SimulatedBrokerStore
from apps.simulated_worker.scenarios import WorkerScenario
from packages.domain.models import (
    BrokerOrderEvent,
    ExternalOrderStatus,
    Money,
    StatusQueryOutcome,
)
from packages.protocol.codec import encode_envelope
from packages.protocol.envelope import EndpointRole, Envelope, MessageType
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.protocol.messages import (
    WorkerCapabilities,
    parse_order_status_request,
    parse_order_submit,
)
from packages.protocol.transport import FramedSocket


class SimulatedWorkerServer:
    """Protocol translator only; it has no strategy, risk or persistence references."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        protocol_version: int,
        scenario: WorkerScenario,
        broker_store_path: Path,
        connect_timeout: float = 2.0,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("simulated worker IPC must use IPv4 loopback")
        self._host = host
        self._port = port
        self._protocol_version = protocol_version
        self._scenario = scenario
        self._connect_timeout = connect_timeout
        self._store = SimulatedBrokerStore(broker_store_path)
        self._capabilities = WorkerCapabilities(
            broker="simulated",
            account_modes=("practice",),
            products=("DIGITAL_OPTION",),
            supports_reconciliation=True,
            supports_quotes=False,
            supports_order_status_query=True,
            worker_version="0.1.0",
            supports_order_events=True,
        )
        self._lifecycle_threads: list[threading.Thread] = []
        self._connection: socket.socket | None = None

    def run(self) -> int:
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._connection = connection
        connection.settimeout(self._connect_timeout)
        connection.connect((self._host, self._port))
        connection.settimeout(None)
        transport = FramedSocket(connection)
        self._log("ipc_connected")
        try:
            if not self._handshake(transport):
                return 2
            self._log("ipc_handshake_completed")
            if self._scenario is WorkerScenario.CRASH_AFTER_HANDSHAKE:
                os._exit(72)
            while True:
                envelope = transport.receive()
                self._validate_routing(envelope)
                if envelope.message_type is MessageType.PING:
                    self._reply(transport, envelope, MessageType.PONG, {})
                elif envelope.message_type is MessageType.WORKER_HEALTH_REQUEST:
                    self._reply(
                        transport,
                        envelope,
                        MessageType.WORKER_HEALTH_RESPONSE,
                        {"status": "READY"},
                    )
                elif envelope.message_type is MessageType.ORDER_SUBMIT:
                    if not self._handle_order(transport, envelope):
                        return 0
                elif envelope.message_type is MessageType.ORDER_STATUS_REQUEST:
                    self._handle_status_query(transport, envelope)
                elif envelope.message_type is MessageType.SHUTDOWN:
                    if self._scenario is WorkerScenario.SHUTDOWN_HANG:
                        threading.Event().wait(60.0)
                        return 0
                    self._reply(transport, envelope, MessageType.SHUTDOWN_ACK, {})
                    self._log("worker_shutdown_completed", envelope)
                    return 0
                else:
                    raise ProtocolError(
                        ProtocolErrorCode.IPC_UNKNOWN_MESSAGE_TYPE,
                        "message type is not accepted by simulated worker",
                    )
        except ProtocolError as exc:
            self._log("ipc_frame_rejected", reason_code=exc.code.value)
            return 3
        finally:
            transport.close()
            self._store.close()

    def _handshake(self, transport: FramedSocket) -> bool:
        hello = transport.receive()
        if (
            hello.message_type is not MessageType.HELLO
            or hello.source is not EndpointRole.CORE
            or hello.target is not EndpointRole.SIMULATED_WORKER
        ):
            raise ProtocolError(
                ProtocolErrorCode.IPC_ROLE_MISMATCH,
                "first worker message must be CORE HELLO",
            )
        self._reply(
            transport,
            hello,
            MessageType.HELLO_ACK,
            self._capabilities.to_payload(),
            protocol_version=self._protocol_version,
        )
        return hello.protocol_version == self._protocol_version

    def _validate_routing(self, envelope: Envelope) -> None:
        if (
            envelope.protocol_version != self._protocol_version
            or envelope.source is not EndpointRole.CORE
            or envelope.target is not EndpointRole.SIMULATED_WORKER
        ):
            raise ProtocolError(
                ProtocolErrorCode.IPC_ROLE_MISMATCH,
                "invalid Core to worker routing",
            )

    def _handle_order(self, transport: FramedSocket, envelope: Envelope) -> bool:
        command = parse_order_submit(envelope)
        self._log("order_command_received", envelope)
        now = datetime.now(UTC)
        if command.deadline_at <= now:
            self._order_reply(
                transport,
                envelope,
                MessageType.ORDER_REJECTED,
                command.order_id,
                reason_code=ProtocolErrorCode.ORDER_COMMAND_EXPIRED.value,
            )
            return True
        if self._scenario is WorkerScenario.DELAY_BEFORE_SEND:
            time.sleep(0.15)
            if command.deadline_at <= datetime.now(UTC):
                self._order_reply(
                    transport,
                    envelope,
                    MessageType.ORDER_REJECTED,
                    command.order_id,
                    reason_code=ProtocolErrorCode.ORDER_COMMAND_EXPIRED.value,
                )
                return True
        if self._scenario is WorkerScenario.CRASH_AFTER_RECEIVE:
            os._exit(73)
        if self._scenario is WorkerScenario.HANG_AFTER_RECEIVE:
            threading.Event().wait(60.0)
            return False
        external_status = ExternalOrderStatus.ACCEPTED
        realized_pnl_minor: int | None = None
        if self._scenario in {
            WorkerScenario.REJECT,
            WorkerScenario.REJECT_BUT_DROP_RESPONSE,
        }:
            external_status = ExternalOrderStatus.REJECTED
        elif self._scenario is WorkerScenario.ACCEPT_AND_SETTLE_BUT_DROP_RESPONSE:
            external_status = ExternalOrderStatus.SETTLED
            realized_pnl_minor = 250
        elif self._scenario is WorkerScenario.SETTLEMENT_UNKNOWN_BUT_DROP_RESPONSE:
            external_status = ExternalOrderStatus.SETTLEMENT_UNKNOWN
        evidence = self._store.record_submission(
            command,
            external_status,
            realized_pnl_minor=realized_pnl_minor,
        )
        if self._scenario in {
            WorkerScenario.ACCEPT_BUT_DROP_RESPONSE,
            WorkerScenario.REJECT_BUT_DROP_RESPONSE,
            WorkerScenario.ACCEPT_AND_SETTLE_BUT_DROP_RESPONSE,
            WorkerScenario.SETTLEMENT_UNKNOWN_BUT_DROP_RESPONSE,
        }:
            os._exit(74)
        if self._scenario is WorkerScenario.REJECT:
            self._order_reply(
                transport,
                envelope,
                MessageType.ORDER_REJECTED,
                command.order_id,
                reason_code="SIMULATED_REJECTION",
            )
            return True
        response_message_id = str(uuid4())
        accepted = self._order_reply(
            transport,
            envelope,
            MessageType.ORDER_ACCEPTED,
            command.order_id,
            response_message_id=response_message_id,
            broker_order_id=evidence.broker_order_id,
        )
        if self._scenario in {
            WorkerScenario.NORMAL_LIFECYCLE,
            WorkerScenario.DUPLICATE_ACCEPTED_EVENT,
            WorkerScenario.DUPLICATE_SETTLED_EVENT,
            WorkerScenario.OUT_OF_ORDER_EVENT,
            WorkerScenario.DROP_OPEN_EVENT,
            WorkerScenario.DROP_SETTLED_EVENT,
            WorkerScenario.CRASH_BEFORE_SETTLED_EVENT,
            WorkerScenario.CRASH_DURING_EVENT_WRITE,
            WorkerScenario.SETTLEMENT_UNKNOWN_EVENT,
            WorkerScenario.CONFLICTING_SETTLEMENT_EVENT,
            WorkerScenario.SEQUENCE_GAP_EVENT,
        }:
            lifecycle = threading.Thread(
                target=self._run_lifecycle,
                args=(transport, command.order_id),
                name=f"simulated-lifecycle-{command.order_id}",
                daemon=True,
            )
            self._lifecycle_threads.append(lifecycle)
            lifecycle.start()
        if self._scenario is WorkerScenario.DUPLICATE_ACCEPT:
            transport.send(accepted)
        elif self._scenario is WorkerScenario.CONFLICTING_DUPLICATE:
            self._order_reply(
                transport,
                envelope,
                MessageType.ORDER_REJECTED,
                command.order_id,
                response_message_id=response_message_id,
                reason_code="CONFLICTING_REPLAY",
            )
        return self._scenario is not WorkerScenario.ACCEPT_AND_EXIT

    def _run_lifecycle(self, transport: FramedSocket, order_id: str) -> None:
        accepted = self._store.record_lifecycle_event(order_id, ExternalOrderStatus.ACCEPTED, 1)
        opened = self._store.record_lifecycle_event(order_id, ExternalOrderStatus.OPEN, 2)
        terminal_status = (
            ExternalOrderStatus.SETTLEMENT_UNKNOWN
            if self._scenario is WorkerScenario.SETTLEMENT_UNKNOWN_EVENT
            else ExternalOrderStatus.SETTLED
        )
        settled = self._store.record_lifecycle_event(
            order_id,
            terminal_status,
            3,
            realized_pnl_minor=(250 if terminal_status is ExternalOrderStatus.SETTLED else None),
        )
        time.sleep(0.02)
        if self._scenario is WorkerScenario.OUT_OF_ORDER_EVENT:
            delivery = [settled, opened, accepted]
        elif self._scenario in {
            WorkerScenario.DROP_OPEN_EVENT,
            WorkerScenario.SEQUENCE_GAP_EVENT,
        }:
            delivery = [accepted, settled]
        elif self._scenario in {
            WorkerScenario.DROP_SETTLED_EVENT,
            WorkerScenario.CRASH_BEFORE_SETTLED_EVENT,
            WorkerScenario.CRASH_DURING_EVENT_WRITE,
        }:
            delivery = [accepted, opened]
        else:
            delivery = [accepted, opened, settled]
        if self._scenario is WorkerScenario.DUPLICATE_ACCEPTED_EVENT:
            delivery.insert(1, accepted)
        if self._scenario is WorkerScenario.DUPLICATE_SETTLED_EVENT:
            delivery.extend([settled] * 99)
        for event in delivery:
            self._send_order_event(transport, event)
            time.sleep(0.005)
        if self._scenario is WorkerScenario.CONFLICTING_SETTLEMENT_EVENT:
            payload = settled.to_payload()
            payload["result_minor"] = -1000
            canonical = {key: value for key, value in payload.items() if key != "evidence_hash"}
            payload["evidence_hash"] = BrokerOrderEvent.evidence_hash_for_payload(canonical)
            self._send_order_event(transport, BrokerOrderEvent.from_payload(payload))
        if self._scenario in {
            WorkerScenario.CRASH_BEFORE_SETTLED_EVENT,
        }:
            os._exit(75)
        if self._scenario is WorkerScenario.CRASH_DURING_EVENT_WRITE:
            self._write_truncated_event_and_exit(settled)

    def _send_order_event(
        self,
        transport: FramedSocket,
        event: BrokerOrderEvent,
    ) -> None:
        envelope = Envelope(
            protocol_version=self._protocol_version,
            message_id=str(uuid4()),
            correlation_id=event.correlation_id,
            causation_id=None,
            source=EndpointRole.SIMULATED_WORKER,
            target=EndpointRole.CORE,
            message_type=MessageType.ORDER_EVENT,
            created_at_utc=datetime.now(UTC),
            deadline_at=None,
            payload=event.to_payload(),
        )
        transport.send(envelope)
        self._store.mark_event_delivered(event.event_id)
        self._log("message_sent_order_event", envelope)

    def _write_truncated_event_and_exit(self, event: BrokerOrderEvent) -> None:
        connection = self._connection
        if connection is None:
            os._exit(76)
        envelope = Envelope(
            protocol_version=self._protocol_version,
            message_id=str(uuid4()),
            correlation_id=event.correlation_id,
            causation_id=None,
            source=EndpointRole.SIMULATED_WORKER,
            target=EndpointRole.CORE,
            message_type=MessageType.ORDER_EVENT,
            created_at_utc=datetime.now(UTC),
            deadline_at=None,
            payload=event.to_payload(),
        )
        encoded = encode_envelope(envelope)
        connection.sendall(struct.pack(">I", len(encoded)) + encoded[: len(encoded) // 2])
        os._exit(76)

    def _handle_status_query(self, transport: FramedSocket, envelope: Envelope) -> None:
        query = parse_order_status_request(envelope)
        self._log("order_status_requested", envelope)
        if self._scenario is WorkerScenario.STATUS_QUERY_TIMEOUT:
            self._store.record_status_query()
            threading.Event().wait(60.0)
            return
        if self._scenario is WorkerScenario.STATUS_NOT_FOUND:
            self._store.record_status_query()
            evidence = None
        else:
            evidence = self._store.query_order(query)
        if evidence is not None:
            if self._scenario is WorkerScenario.STATUS_CONFLICT_ACCOUNT:
                evidence = replace(evidence, account_id=f"{evidence.account_id}-OTHER")
            elif self._scenario is WorkerScenario.STATUS_CONFLICT_AMOUNT:
                evidence = replace(
                    evidence,
                    amount=Money(evidence.amount.minor_units + 1, evidence.amount.currency),
                )
            elif self._scenario is WorkerScenario.STATUS_CONFLICT_CURRENCY:
                evidence = replace(
                    evidence,
                    amount=Money(evidence.amount.minor_units, "EUR"),
                )
            elif self._scenario is WorkerScenario.STATUS_CONFLICT_SYMBOL:
                evidence = replace(evidence, symbol=f"{evidence.symbol}-OTHER")
            elif self._scenario is WorkerScenario.STATUS_CONFLICT_BROKER_ID:
                evidence = replace(evidence, broker_order_id="SIM-CONFLICTING-ID")
        payload: dict[str, object] = {
            "query_outcome": (
                StatusQueryOutcome.FOUND.value
                if evidence is not None
                else StatusQueryOutcome.NOT_FOUND.value
            ),
            "evidence": evidence.to_payload() if evidence is not None else None,
            "reason_code": (
                None if evidence is not None else ProtocolErrorCode.RECONCILIATION_NOT_FOUND.value
            ),
        }
        response = self._reply(
            transport,
            envelope,
            MessageType.ORDER_STATUS_RESPONSE,
            payload,
        )
        if self._scenario is WorkerScenario.STATUS_QUERY_DUPLICATE:
            transport.send(response)

    def _order_reply(
        self,
        transport: FramedSocket,
        request: Envelope,
        message_type: MessageType,
        order_id: str,
        *,
        response_message_id: str | None = None,
        broker_order_id: str | None = None,
        reason_code: str | None = None,
    ) -> Envelope:
        payload: dict[str, object] = {
            "order_id": order_id,
            "broker_order_id": broker_order_id,
            "reason_code": reason_code,
        }
        return self._reply(
            transport,
            request,
            message_type,
            payload,
            message_id=response_message_id,
        )

    def _reply(
        self,
        transport: FramedSocket,
        request: Envelope,
        message_type: MessageType,
        payload: dict[str, object],
        *,
        protocol_version: int | None = None,
        message_id: str | None = None,
    ) -> Envelope:
        response = Envelope(
            protocol_version=protocol_version or self._protocol_version,
            message_id=message_id or str(uuid4()),
            correlation_id=request.correlation_id,
            causation_id=request.message_id,
            source=EndpointRole.SIMULATED_WORKER,
            target=EndpointRole.CORE,
            message_type=message_type,
            created_at_utc=datetime.now(UTC),
            deadline_at=None,
            payload=payload,
        )
        transport.send(response)
        self._log(f"message_sent_{message_type.value.lower()}", response)
        return response

    @staticmethod
    def _log(
        event: str,
        envelope: Envelope | None = None,
        *,
        reason_code: str | None = None,
    ) -> None:
        record = {
            "process": "simulated_worker",
            "worker_type": "SIMULATED",
            "event": event,
            "message_id": envelope.message_id if envelope else None,
            "correlation_id": envelope.correlation_id if envelope else None,
            "reason_code": reason_code,
        }
        print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)
