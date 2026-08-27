from __future__ import annotations

import socket
import struct
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.core.coordinator import OrderCoordinator
from apps.core.health import HealthGate
from apps.core.recovery import RecoveryCoordinator
from apps.core.worker_client import SocketWorkerClient
from apps.core.worker_supervisor import (
    CircuitState,
    RestartPolicy,
    WorkerHealthState,
    WorkerSupervisor,
)
from apps.simulated_worker.scenarios import WorkerScenario
from packages.domain.models import Broker, Direction, Money, OrderCommand, OrderRequest
from packages.observability.events import EventValue, InMemoryEventSink
from packages.persistence.reader import StateReader
from packages.persistence.writer import SingleDatabaseWriter
from packages.protocol.envelope import EndpointRole, Envelope, MessageType
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.protocol.messages import WorkerCapabilities
from packages.protocol.transport import FramedSocket
from packages.protocol.version import MAX_FRAME_SIZE, PROTOCOL_VERSION


def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before deadline")


def request(suffix: str, *, deadline_at: datetime | None = None) -> OrderRequest:
    return OrderRequest(
        correlation_id=f"corr-{suffix}",
        broker=Broker.DERIV,
        account_id=f"account-{suffix}",
        product="DIGITAL_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1_000, "USD"),
        strategy_id="strategy-contract",
        strategy_version="1.0.0",
        deadline_at=deadline_at or datetime.now(UTC) + timedelta(seconds=5),
    )


def command(suffix: str, *, deadline_at: datetime | None = None) -> OrderCommand:
    return OrderCommand(
        message_id=f"message-{suffix}",
        correlation_id=f"corr-{suffix}",
        intent_id=f"intent-{suffix}",
        order_id=f"order-{suffix}",
        broker=Broker.DERIV,
        account_id=f"account-{suffix}",
        product="DIGITAL_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1_000, "USD"),
        deadline_at=deadline_at or datetime.now(UTC) + timedelta(seconds=5),
    )


def test_ipc_01_handshake_v1_capabilities_health_and_response_correlation() -> None:
    gate = HealthGate()
    supervisor = WorkerSupervisor(gate, heartbeat_interval=10.0)
    client = supervisor.start()
    try:
        assert supervisor.process is not None
        assert supervisor.process.pid != 0
        assert supervisor.health_state is WorkerHealthState.READY
        assert client.capabilities.broker == "simulated"
        assert client.capabilities.account_modes == ("practice",)
        assert client.request_health() == "READY"
        result = client.submit_order(command("round-trip"))
        assert result.correlation_id == "corr-round-trip"
        assert result.causation_id == "message-round-trip"
        assert result.broker_order_id == "SIM-message-round-trip"
    finally:
        supervisor.shutdown()


def test_ipc_02_incompatible_worker_is_blocked_before_financial_command() -> None:
    gate = HealthGate()
    supervisor = WorkerSupervisor(
        gate,
        worker_protocol_version=999,
        heartbeat_interval=10.0,
    )
    with pytest.raises(ProtocolError) as captured:
        supervisor.start()
    assert captured.value.code is ProtocolErrorCode.IPC_PROTOCOL_INCOMPATIBLE
    assert supervisor.health_state is WorkerHealthState.INCOMPATIBLE
    assert gate.state.reason_code == "HG_WORKER_INCOMPATIBLE"


def test_contract_worker_rejects_oversized_frame_and_exits() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = int(listener.getsockname()[1])
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "apps.simulated_worker",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--scenario",
            WorkerScenario.ACCEPT.value,
            "--protocol-version",
            str(PROTOCOL_VERSION),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    connection, _ = listener.accept()
    listener.close()
    transport = FramedSocket(connection)
    hello = Envelope(
        protocol_version=PROTOCOL_VERSION,
        message_id="hello-message",
        correlation_id="hello-correlation",
        causation_id=None,
        source=EndpointRole.CORE,
        target=EndpointRole.SIMULATED_WORKER,
        message_type=MessageType.HELLO,
        created_at_utc=datetime.now(UTC),
        deadline_at=None,
        payload={"client_role": "CORE"},
    )
    transport.send(hello)
    assert transport.receive().message_type is MessageType.HELLO_ACK
    connection.sendall(struct.pack("!I", MAX_FRAME_SIZE + 1))
    assert process.wait(timeout=2.0) == 3
    transport.close()


def test_ipc_09_worker_deadline_expired_before_simulated_send_is_rejected() -> None:
    supervisor = WorkerSupervisor(HealthGate(), heartbeat_interval=10.0)
    client = supervisor.start()
    try:
        result = client.submit_order(
            command("expired", deadline_at=datetime.now(UTC) - timedelta(milliseconds=1))
        )
        assert result.reason_code == ProtocolErrorCode.ORDER_COMMAND_EXPIRED.value
        assert result.outcome.value == "REJECTED"
        assert result.broker_order_id is None
    finally:
        supervisor.shutdown()


def test_deadline_expiring_during_pre_send_delay_is_proven_rejected() -> None:
    supervisor = WorkerSupervisor(
        HealthGate(),
        scenario=WorkerScenario.DELAY_BEFORE_SEND,
        heartbeat_interval=10.0,
    )
    client = supervisor.start()
    try:
        result = client.submit_order(
            command("delay-expired", deadline_at=datetime.now(UTC) + timedelta(milliseconds=50))
        )
        assert result.outcome.value == "REJECTED"
        assert result.reason_code == ProtocolErrorCode.ORDER_COMMAND_EXPIRED.value
    finally:
        supervisor.shutdown()


def test_ipc_11_duplicate_acceptance_frame_is_idempotent() -> None:
    supervisor = WorkerSupervisor(
        HealthGate(),
        scenario=WorkerScenario.DUPLICATE_ACCEPT,
        heartbeat_interval=10.0,
    )
    client = supervisor.start()
    try:
        assert client.submit_order(command("duplicate")).outcome.value == "ACCEPTED"
        wait_until(lambda: client.duplicate_count == 1)
        assert client.is_ready
    finally:
        supervisor.shutdown()


def test_conflicting_replay_message_id_is_protocol_violation() -> None:
    events = InMemoryEventSink()
    supervisor = WorkerSupervisor(
        HealthGate(),
        scenario=WorkerScenario.CONFLICTING_DUPLICATE,
        event_sink=events,
        heartbeat_interval=10.0,
    )
    client = supervisor.start()
    try:
        assert client.submit_order(command("conflict")).outcome.value == "ACCEPTED"
        wait_until(lambda: not client.is_ready)
        assert any(
            event.reason_code == ProtocolErrorCode.IPC_MESSAGE_REPLAY_CONFLICT.value
            for event in events.events
        )
    finally:
        supervisor.shutdown()


def test_ipc_12_worker_crash_before_dispatch_keeps_core_and_outbox_conservative(
    tmp_path: Path,
) -> None:
    supervisor_gate = HealthGate()
    supervisor = WorkerSupervisor(
        supervisor_gate,
        scenario=WorkerScenario.CRASH_AFTER_HANDSHAKE,
        heartbeat_interval=0.05,
        heartbeat_timeout=0.05,
    )
    client = supervisor.start()
    wait_until(lambda: supervisor.health_state is WorkerHealthState.DISCONNECTED)
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    reader = StateReader(tmp_path / "state.db")
    core_gate = HealthGate()
    coordinator = OrderCoordinator(writer, client, core_gate)
    try:
        persisted = coordinator.submit(request("crash-before"))
        assert reader.one("outbox_messages", "message_id", persisted.message_id)["state"] == (
            "BLOCKED_NOT_SENT"
        )
        assert reader.one("orders", "order_id", persisted.order_id)["state"] == "SEND_BLOCKED"
        assert (
            reader.one("risk_reservations", "reservation_id", persisted.reservation_id)["state"]
            == "ACTIVE"
        )
        assert core_gate.state.reason_code == "HG_WORKER_NOT_READY"
    finally:
        writer.close()
        supervisor.shutdown()


def test_ipc_13_and_14_crash_after_possible_send_is_unknown_with_active_reservation(
    tmp_path: Path,
) -> None:
    gate = HealthGate()
    supervisor = WorkerSupervisor(
        gate,
        scenario=WorkerScenario.CRASH_AFTER_RECEIVE,
        response_timeout=0.3,
        heartbeat_interval=10.0,
    )
    client = supervisor.start()
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    reader = StateReader(tmp_path / "state.db")
    coordinator = OrderCoordinator(writer, client, gate)
    try:
        persisted = coordinator.submit(request("crash-after"))
        assert reader.one("outbox_messages", "message_id", persisted.message_id)["state"] == (
            "AMBIGUOUS"
        )
        assert reader.one("orders", "order_id", persisted.order_id)["state"] == "UNKNOWN"
        assert (
            reader.one("risk_reservations", "reservation_id", persisted.reservation_id)["state"]
            == "ACTIVE"
        )
        assert gate.state.reason_code == "HG_ORDER_UNKNOWN"
    finally:
        writer.close()
        supervisor.shutdown()


def test_worker_hang_after_receive_times_out_as_unknown(tmp_path: Path) -> None:
    gate = HealthGate()
    supervisor = WorkerSupervisor(
        gate,
        scenario=WorkerScenario.HANG_AFTER_RECEIVE,
        response_timeout=0.1,
        heartbeat_interval=10.0,
    )
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    reader = StateReader(tmp_path / "state.db")
    try:
        persisted = OrderCoordinator(writer, supervisor.start(), gate).submit(request("hang"))
        assert reader.one("orders", "order_id", persisted.order_id)["state"] == "UNKNOWN"
        assert reader.one("outbox_messages", "message_id", persisted.message_id)["state"] == (
            "AMBIGUOUS"
        )
    finally:
        supervisor.shutdown(grace_seconds=0.1)
        writer.close()


def test_proven_worker_rejection_releases_reservation(tmp_path: Path) -> None:
    gate = HealthGate()
    supervisor = WorkerSupervisor(
        gate,
        scenario=WorkerScenario.REJECT,
        heartbeat_interval=10.0,
    )
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    reader = StateReader(tmp_path / "state.db")
    try:
        persisted = OrderCoordinator(writer, supervisor.start(), gate).submit(request("reject"))
        assert reader.one("orders", "order_id", persisted.order_id)["state"] == "REJECTED"
        assert (
            reader.one("risk_reservations", "reservation_id", persisted.reservation_id)["state"]
            == "RELEASED"
        )
        assert reader.one("outbox_messages", "message_id", persisted.message_id)["state"] == (
            "DISPATCHED"
        )
    finally:
        supervisor.shutdown()
        writer.close()


def test_worker_response_is_persisted_before_worker_exit(tmp_path: Path) -> None:
    gate = HealthGate()
    supervisor = WorkerSupervisor(
        gate,
        scenario=WorkerScenario.ACCEPT_AND_EXIT,
        heartbeat_interval=0.05,
        heartbeat_timeout=0.05,
    )
    writer = SingleDatabaseWriter(tmp_path / "state.db")
    reader = StateReader(tmp_path / "state.db")
    try:
        persisted = OrderCoordinator(writer, supervisor.start(), gate).submit(
            request("accept-exit")
        )
        wait_until(lambda: supervisor.health_state is WorkerHealthState.DISCONNECTED)
        assert reader.one("orders", "order_id", persisted.order_id)["state"] == "ACCEPTED"
        assert reader.one("outbox_messages", "message_id", persisted.message_id)["state"] == (
            "DISPATCHED"
        )
    finally:
        supervisor.shutdown()
        writer.close()


def test_ipc_15_worker_restart_never_resends_ambiguous_message(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    gate = HealthGate()
    first = WorkerSupervisor(
        gate,
        scenario=WorkerScenario.CRASH_AFTER_RECEIVE,
        response_timeout=0.3,
        heartbeat_interval=10.0,
    )
    writer = SingleDatabaseWriter(database_path)
    persisted = OrderCoordinator(writer, first.start(), gate).submit(request("restart-ambiguous"))
    writer.close()
    first.shutdown()

    restarted_writer = SingleDatabaseWriter(database_path)
    reader = StateReader(database_path)
    restarted_gate = HealthGate()
    report = RecoveryCoordinator(restarted_writer, reader, restarted_gate).recover()
    second = WorkerSupervisor(restarted_gate, heartbeat_interval=10.0)
    second.start()
    try:
        assert report.ambiguous_message_ids == (persisted.message_id,)
        outbox = reader.one("outbox_messages", "message_id", persisted.message_id)
        assert outbox["state"] == "AMBIGUOUS"
        assert outbox["attempt_count"] == 1
        assert restarted_gate.state.reason_code == "HG_ORDER_UNKNOWN"
    finally:
        second.shutdown()
        restarted_writer.close()


def test_ipc_16_heartbeat_detects_abrupt_worker_kill() -> None:
    gate = HealthGate()
    supervisor = WorkerSupervisor(
        gate,
        heartbeat_interval=0.05,
        heartbeat_timeout=0.05,
    )
    supervisor.start()
    process = supervisor.process
    assert process is not None
    process.kill()
    process.wait(timeout=1.0)
    wait_until(lambda: supervisor.health_state is WorkerHealthState.DISCONNECTED)
    assert gate.state.reason_code == "HG_WORKER_DISCONNECTED"
    supervisor.shutdown()


def test_ipc_17_circuit_breaker_prevents_restart_loop() -> None:
    supervisor = WorkerSupervisor(
        HealthGate(),
        scenario=WorkerScenario.CRASH_AFTER_HANDSHAKE,
        heartbeat_interval=0.02,
        heartbeat_timeout=0.02,
        restart_policy=RestartPolicy(max_crashes=2, open_seconds=10.0),
        sleeper=lambda _: None,
    )
    supervisor.start()
    # Recovery is automatic; repeated crashes open the breaker without a manual kick.
    wait_until(lambda: supervisor.circuit_state is CircuitState.OPEN)
    with pytest.raises(RuntimeError, match="circuit breaker"):
        supervisor.restart()
    supervisor.shutdown()


def test_worker_circuit_half_open_probe_recovers_without_manual_restart() -> None:
    gate = HealthGate()
    supervisor = WorkerSupervisor(
        gate,
        scenario=WorkerScenario.CRASH_AFTER_HANDSHAKE,
        heartbeat_interval=0.02,
        heartbeat_timeout=0.02,
        restart_policy=RestartPolicy(
            max_crashes=1,
            open_seconds=0.1,
            base_delay_seconds=0.01,
            max_delay_seconds=0.02,
        ),
        jitter=lambda _ceiling: 0.0,
    )
    supervisor.start()
    wait_until(lambda: supervisor.circuit_state is CircuitState.OPEN)

    supervisor._scenario = WorkerScenario.ACCEPT
    wait_until(
        lambda: (
            supervisor.health_state is WorkerHealthState.READY
            and supervisor.circuit_state is CircuitState.CLOSED
        )
    )
    assert not gate.contains("HG_WORKER_CIRCUIT_OPEN")
    assert not gate.contains("HG_WORKER_DISCONNECTED")
    supervisor.shutdown()


def test_ipc_18_graceful_shutdown_ack_terminates_subprocess() -> None:
    supervisor = WorkerSupervisor(HealthGate(), heartbeat_interval=10.0)
    supervisor.start()
    process = supervisor.process
    assert process is not None and process.poll() is None
    supervisor.shutdown()
    assert process.poll() == 0
    assert supervisor.last_shutdown_forced is False
    assert supervisor.health_state is WorkerHealthState.STOPPED


def test_ipc_19_shutdown_timeout_forces_process_termination() -> None:
    supervisor = WorkerSupervisor(
        HealthGate(),
        scenario=WorkerScenario.SHUTDOWN_HANG,
        heartbeat_interval=10.0,
    )
    supervisor.start()
    process = supervisor.process
    assert process is not None
    supervisor.shutdown(grace_seconds=0.1)
    assert process.poll() is not None
    assert supervisor.last_shutdown_forced is True


def test_ipc_20_two_workers_have_independent_process_and_connection_state() -> None:
    first = WorkerSupervisor(HealthGate(), heartbeat_interval=10.0)
    second = WorkerSupervisor(HealthGate(), heartbeat_interval=10.0)
    first.start()
    second.start()
    try:
        assert first.process is not None and second.process is not None
        assert first.process.pid != second.process.pid
        first.process.kill()
        first.process.wait(timeout=1.0)
        wait_until(lambda: first.health_state is WorkerHealthState.DISCONNECTED)
        assert second.health_state is WorkerHealthState.READY
        second.heartbeat()
    finally:
        first.shutdown()
        second.shutdown()


def test_ipc_21_worker_process_receives_no_database_path(tmp_path: Path) -> None:
    state_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(state_path)
    supervisor = WorkerSupervisor(HealthGate(), heartbeat_interval=10.0)
    supervisor.start()
    try:
        process = supervisor.process
        assert process is not None
        arguments = tuple(str(item) for item in process.args)
        assert str(state_path) not in arguments
        broker_store_argument = arguments[arguments.index("--broker-store") + 1]
        assert broker_store_argument != str(state_path)
        assert "dualtrade-simulated-broker-" in broker_store_argument
    finally:
        supervisor.shutdown()
        writer.close()


def test_ipc_22_persist_before_act_is_true_at_send_boundary(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    reader = StateReader(database_path)

    class CommitProofSink(InMemoryEventSink):
        proof_observed = False

        def emit(
            self,
            event_name: str,
            *,
            reason_code: str | None = None,
            **fields: EventValue,
        ) -> None:
            if event_name == "order_command_send_started":
                assert reader.count("trade_intents") == 1
                assert reader.count("risk_reservations") == 1
                assert reader.count("orders") == 1
                assert reader.count("outbox_messages") == 1
                self.proof_observed = True
            super().emit(event_name, reason_code=reason_code, **fields)

    events = CommitProofSink()
    gate = HealthGate()
    supervisor = WorkerSupervisor(gate, event_sink=events, heartbeat_interval=10.0)
    writer = SingleDatabaseWriter(database_path)
    try:
        OrderCoordinator(writer, supervisor.start(), gate).submit(request("persist-first"))
        assert events.proof_observed
    finally:
        supervisor.shutdown()
        writer.close()


def test_ipc_23_core_database_survives_worker_kill(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    gate = HealthGate()
    supervisor = WorkerSupervisor(
        gate,
        heartbeat_interval=0.05,
        heartbeat_timeout=0.05,
    )
    writer = SingleDatabaseWriter(database_path)
    reader = StateReader(database_path)
    persisted = OrderCoordinator(writer, supervisor.start(), gate).submit(request("core-alive"))
    process = supervisor.process
    assert process is not None
    process.kill()
    process.wait(timeout=1.0)
    wait_until(lambda: supervisor.health_state is WorkerHealthState.DISCONNECTED)
    assert reader.one("orders", "order_id", persisted.order_id)["state"] == "ACCEPTED"
    writer.run_integrity_check()
    supervisor.shutdown()
    writer.close()


def test_ipc_24_bounded_queue_saturation_degrades_without_silent_drop() -> None:
    core_socket, worker_socket = socket.socketpair()
    events = InMemoryEventSink()
    capabilities = WorkerCapabilities(
        broker="simulated",
        account_modes=("practice",),
        products=("DIGITAL_OPTION",),
        supports_reconciliation=True,
        supports_quotes=False,
        supports_order_status_query=True,
        worker_version="test",
    )
    client = SocketWorkerClient(
        FramedSocket(core_socket),
        capabilities,
        event_queue_size=1,
        event_sink=events,
    )
    transport = FramedSocket(worker_socket)
    try:
        for index in range(2):
            transport.send(
                Envelope(
                    protocol_version=PROTOCOL_VERSION,
                    message_id=f"async-{index}",
                    correlation_id=f"async-corr-{index}",
                    causation_id=None,
                    source=EndpointRole.SIMULATED_WORKER,
                    target=EndpointRole.CORE,
                    message_type=MessageType.ORDER_EVENT,
                    created_at_utc=datetime.now(UTC),
                    deadline_at=None,
                    payload={"status": "READY"},
                )
            )
        wait_until(lambda: not client.is_ready)
        assert any(
            event.reason_code == ProtocolErrorCode.IPC_BACKPRESSURE.value for event in events.events
        )
    finally:
        client.close()
        transport.close()


def test_late_non_event_worker_response_is_ignored_without_poisoning_financial_queue() -> None:
    core_socket, worker_socket = socket.socketpair()
    events = InMemoryEventSink()
    capabilities = WorkerCapabilities(
        broker="simulated",
        account_modes=("practice",),
        products=("DIGITAL_OPTION",),
        supports_reconciliation=True,
        supports_quotes=False,
        supports_order_status_query=True,
        worker_version="test",
    )
    client = SocketWorkerClient(
        FramedSocket(core_socket),
        capabilities,
        event_queue_size=1,
        event_sink=events,
    )
    transport = FramedSocket(worker_socket)
    try:
        transport.send(
            Envelope(
                protocol_version=PROTOCOL_VERSION,
                message_id="late-health-response",
                correlation_id="late-health-correlation",
                causation_id="expired-request",
                source=EndpointRole.SIMULATED_WORKER,
                target=EndpointRole.CORE,
                message_type=MessageType.WORKER_HEALTH_RESPONSE,
                created_at_utc=datetime.now(UTC),
                deadline_at=None,
                payload={"status": "READY"},
            )
        )
        wait_until(
            lambda: any(
                event.event_name == "late_worker_response_ignored" for event in events.events
            )
        )
        assert client.is_ready
        assert client.pending_order_event_count == 0
    finally:
        client.close()
        transport.close()
