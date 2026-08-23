from __future__ import annotations

import secrets
import threading

import pytest

from apps.core.lifecycle_server import CoreLifecycleServer
from apps.core.lifecycle_service import CoreServiceState
from apps.launcher.core_client import CoreLifecycleClient, CoreLifecycleIpcUnavailable
from packages.protocol import LifecycleProcessStatus
from packages.security import SecretValue


class FakeLifecycleService:
    def __init__(self) -> None:
        self.state = CoreServiceState.READY
        self.safe_stop_active = False
        self.events: list[str] = []

    def process_statuses(self) -> tuple[LifecycleProcessStatus, ...]:
        return tuple(
            LifecycleProcessStatus(role, index + 10, True, None, "READY", 0)
            for index, role in enumerate(("AUTH_AGENT", "CORE", "SIMULATED_WORKER", "DERIV_WORKER"))
        )

    def safe_stop(self) -> None:
        self.events.append("safe_stop")
        self.safe_stop_active = True
        self.state = CoreServiceState.SAFE_STOP

    def drain(self, timeout: float) -> tuple[bool, int]:
        assert timeout > 0
        self.events.append("drain")
        return True, 0

    def shutdown_workers(self, grace_seconds: float) -> bool:
        assert grace_seconds > 0
        self.events.append("workers")
        return True

    def shutdown_auth(self, grace_seconds: float) -> None:
        assert grace_seconds > 0
        self.events.append("auth")

    def restart_component(self, role: str) -> tuple[bool, str]:
        self.events.append(f"restart:{role}")
        return True, "RESTART_COMPLETED"

    def shutdown_core(self) -> None:
        self.events.append("core")
        self.state = CoreServiceState.STOPPED


def test_lifecycle_handshake_rejects_wrong_spawn_capability() -> None:
    token = SecretValue.from_text(secrets.token_hex(32))
    service = FakeLifecycleService()
    server = CoreLifecycleServer(service, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(CoreLifecycleIpcUnavailable):
            CoreLifecycleClient.connect(
                server.port,
                SecretValue.from_text(secrets.token_hex(32)),
            )
        client = CoreLifecycleClient.connect(server.port, token)
        assert client.status().core_state == "READY"
        assert client.shutdown_core(1.0)
    finally:
        server.stop()
        thread.join(timeout=2.0)


def test_lifecycle_control_preserves_safe_shutdown_order() -> None:
    token = SecretValue.from_text(secrets.token_hex(32))
    service = FakeLifecycleService()
    server = CoreLifecycleServer(service, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = CoreLifecycleClient.connect(server.port, token)
    try:
        assert client.safe_stop()
        assert client.drain(0.1).drained
        assert client.shutdown_workers(1.0)
        assert client.shutdown_auth(1.0)
        assert client.shutdown_core(1.0)
        assert service.events == ["safe_stop", "drain", "workers", "auth", "core"]
    finally:
        client.close()
        server.stop()
        thread.join(timeout=2.0)
