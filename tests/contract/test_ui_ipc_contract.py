from __future__ import annotations

import secrets

import pytest

from apps.core.ui_service import CoreUiProjectionService
from apps.ui.ipc_client import UiIpcClient, UiIpcUnavailable
from packages.protocol import (
    BrokerCardStatus,
    HealthGateStatus,
    UiAccountMode,
    UiGlobalState,
    UiProjectionSnapshot,
)
from packages.security import SecretValue


def _snapshot() -> UiProjectionSnapshot:
    return UiProjectionSnapshot(
        UiGlobalState.READY,
        False,
        (HealthGateStatus("GLOBAL_ENTRY_GATE", True, None, "Ready"),),
        (BrokerCardStatus("SIMULATED", UiAccountMode.PRACTICE, True, None, None, False),),
        (),
        0,
        None,
    )


def test_ui_ipc_authentication_projection_and_commands_are_bounded() -> None:
    token = SecretValue.from_text(secrets.token_hex(32))
    events: list[str] = []
    safe_stop = False

    def stop() -> None:
        nonlocal safe_stop
        safe_stop = True
        events.append("safe_stop")

    def resume() -> bool:
        nonlocal safe_stop
        safe_stop = False
        events.append("resume")
        return True

    service = CoreUiProjectionService(
        token,
        _snapshot,
        stop,
        resume,
        lambda: events.append("shutdown_requested"),
    )
    service.start()
    try:
        with pytest.raises(UiIpcUnavailable):
            UiIpcClient.connect(
                service.port,
                SecretValue.from_text(secrets.token_hex(32)),
                request_timeout=0.5,
            )
        client = UiIpcClient.connect(service.port, token)
        try:
            assert client.projection().global_state is UiGlobalState.READY
            assert client.safe_stop().safe_stop_active is True
            assert safe_stop is True
            assert client.resume().accepted is True
            assert safe_stop is False
            assert client.request_shutdown().reason_code == "SAFE_SHUTDOWN_REQUESTED"
            assert events == ["safe_stop", "resume", "safe_stop", "shutdown_requested"]
        finally:
            client.close()
    finally:
        service.stop()
