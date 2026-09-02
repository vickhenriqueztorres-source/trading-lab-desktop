from __future__ import annotations

import threading
from typing import Any, cast

from apps.core.health import HealthGate
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSpec, ReadOnlyWorkerSupervisor
from packages.protocol import EndpointRole


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
