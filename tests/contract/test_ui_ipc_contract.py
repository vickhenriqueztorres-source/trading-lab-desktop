from __future__ import annotations

import secrets
import time

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
        deriv_demo_connect=lambda: (
            events.append("deriv_demo_connect") or True,
            "DERIV_DEMO_CONNECTED",
        ),
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
            assert client.connect_deriv_demo().reason_code == "DERIV_DEMO_CONNECTED"
            assert client.request_shutdown().reason_code == "SAFE_SHUTDOWN_REQUESTED"
            assert events == [
                "safe_stop",
                "resume",
                "deriv_demo_connect",
                "safe_stop",
                "shutdown_requested",
            ]
        finally:
            client.close()
    finally:
        service.stop()


def test_ui_ipc_reconnect_replays_same_command_without_duplicate_side_effect() -> None:
    token = SecretValue.from_text(secrets.token_hex(32))
    calls = 0

    def slow_stop() -> None:
        nonlocal calls
        calls += 1
        time.sleep(0.15)

    service = CoreUiProjectionService(
        token,
        _snapshot,
        slow_stop,
        lambda: True,
        lambda: None,
        request_timeout=0.5,
    )
    service.start()
    try:
        client = UiIpcClient.connect(service.port, token, request_timeout=0.1)
        try:
            assert client.safe_stop().reason_code == "SAFE_STOP_ACTIVE"
            assert calls == 1
        finally:
            client.close()
    finally:
        service.stop()


def test_ui_ipc_reconnects_after_idle_server_closes_connection() -> None:
    token = SecretValue.from_text(secrets.token_hex(32))
    service = CoreUiProjectionService(
        token,
        _snapshot,
        lambda: None,
        lambda: True,
        lambda: None,
        request_timeout=0.05,
    )
    service.start()
    try:
        client = UiIpcClient.connect(service.port, token, request_timeout=0.5)
        try:
            time.sleep(0.1)
            assert client.projection().global_state is UiGlobalState.READY
        finally:
            client.close()
    finally:
        service.stop()
