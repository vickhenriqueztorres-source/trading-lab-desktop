from __future__ import annotations

import threading
from typing import Any, cast

from apps.core.health import HealthGate
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSpec, ReadOnlyWorkerSupervisor
from apps.core.worker_client import DeliveryCertainty, WorkerDispatchError
from packages.protocol import EndpointRole
from packages.protocol.errors import ProtocolErrorCode


def test_heartbeat_waits_for_serialized_broker_request_to_finish() -> None:
    pending_observed = threading.Event()
    ping_observed = threading.Event()

    class Process:
        @staticmethod
        def poll() -> None:
            return None

    class Client:
        def __init__(self) -> None:
            self.pending = 1
            self.pending_reads = 0
            self.ping_calls = 0

        @property
        def pending_request_count(self) -> int:
            self.pending_reads += 1
            if self.pending_reads >= 3:
                pending_observed.set()
            return self.pending

        def ping(self, _timeout: float) -> None:
            self.ping_calls += 1
            ping_observed.set()

    supervisor = ReadOnlyWorkerSupervisor(
        HealthGate(),
        ReadOnlyWorkerSpec(
            module="unused",
            role=EndpointRole.IQOPTION_WORKER,
            broker="IQOPTION",
        ),
        heartbeat_interval=0.01,
        heartbeat_timeout=0.1,
    )
    client = Client()
    supervisor._process = cast(Any, Process())
    supervisor._client = cast(Any, client)
    monitor = threading.Thread(target=supervisor._monitor_loop, daemon=True)
    monitor.start()
    try:
        assert pending_observed.wait(0.5)
        assert client.ping_calls == 0

        client.pending = 0
        assert ping_observed.wait(0.5)
        assert client.ping_calls >= 1
    finally:
        supervisor._monitor_stop.set()
        monitor.join(timeout=0.5)


def test_two_transient_heartbeat_failures_do_not_kill_monitor() -> None:
    recovered = threading.Event()
    disconnected: list[ProtocolErrorCode] = []

    class Process:
        @staticmethod
        def poll() -> None:
            return None

    class Client:
        pending_request_count = 0

        def __init__(self) -> None:
            self.calls = 0

        def ping(self, _timeout: float) -> None:
            self.calls += 1
            if self.calls <= 2:
                raise WorkerDispatchError(
                    ProtocolErrorCode.ORDER_DISPATCH_AMBIGUOUS,
                    DeliveryCertainty.NOT_SENT,
                    "transient heartbeat timeout",
                )
            recovered.set()

    supervisor = ReadOnlyWorkerSupervisor(
        HealthGate(),
        ReadOnlyWorkerSpec("unused", EndpointRole.IQOPTION_WORKER, "IQOPTION"),
        heartbeat_interval=0.01,
        heartbeat_timeout=0.1,
        heartbeat_failure_threshold=3,
        disconnect_notifier=lambda _source, code: disconnected.append(code),
    )
    supervisor._process = cast(Any, Process())
    supervisor._client = cast(Any, Client())
    monitor = threading.Thread(target=supervisor._monitor_loop, daemon=True)
    monitor.start()
    try:
        assert recovered.wait(0.5)
        assert disconnected == []
        assert monitor.is_alive()
    finally:
        supervisor._monitor_stop.set()
        monitor.join(timeout=0.5)


def test_heartbeat_exhaustion_notifies_disconnect_once() -> None:
    notified = threading.Event()
    codes: list[ProtocolErrorCode] = []

    class Process:
        @staticmethod
        def poll() -> None:
            return None

    class Client:
        pending_request_count = 0

        @staticmethod
        def ping(_timeout: float) -> None:
            raise WorkerDispatchError(
                ProtocolErrorCode.ORDER_DISPATCH_AMBIGUOUS,
                DeliveryCertainty.NOT_SENT,
                "heartbeat unavailable",
            )

    def on_disconnect(_source: ReadOnlyWorkerSupervisor, code: ProtocolErrorCode) -> None:
        codes.append(code)
        notified.set()

    supervisor = ReadOnlyWorkerSupervisor(
        HealthGate(),
        ReadOnlyWorkerSpec("unused", EndpointRole.IQOPTION_WORKER, "IQOPTION"),
        heartbeat_interval=0.01,
        heartbeat_timeout=0.1,
        heartbeat_failure_threshold=3,
        disconnect_notifier=on_disconnect,
    )
    supervisor._process = cast(Any, Process())
    supervisor._client = cast(Any, Client())
    monitor = threading.Thread(target=supervisor._monitor_loop, daemon=True)
    monitor.start()
    assert notified.wait(0.5)
    monitor.join(timeout=0.5)
    supervisor._on_disconnect(ProtocolErrorCode.WORKER_CRASHED)

    assert codes == [ProtocolErrorCode.IPC_CONNECTION_LOST]
