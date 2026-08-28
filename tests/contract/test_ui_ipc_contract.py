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
        digit_test_session_reset=lambda: (
            events.append("digit_test_session_reset") or True,
            "DIGIT_TEST_SESSION_RESET",
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
            assert client.reset_digit_test_session().reason_code == "DIGIT_TEST_SESSION_RESET"
            assert client.request_shutdown().reason_code == "SAFE_SHUTDOWN_REQUESTED"
            assert events == [
                "safe_stop",
                "resume",
                "deriv_demo_connect",
                "digit_test_session_reset",
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


def test_rejected_arm_reports_daily_stop_and_remains_disarmed() -> None:
    token = SecretValue.from_text(secrets.token_hex(32))

    def blocked_snapshot() -> UiProjectionSnapshot:
        return UiProjectionSnapshot(
            UiGlobalState.RISK_LOCKED,
            True,
            (
                HealthGateStatus("GLOBAL_ENTRY_GATE", False, "HG_SAFE_STOP", "Stopped"),
                HealthGateStatus(
                    "DERIV_READY_TO_ARM",
                    False,
                    "HG_DAILY_STOP_REACHED",
                    "Daily stop reached",
                ),
            ),
            (BrokerCardStatus("DERIV", UiAccountMode.PRACTICE, True, None, None, True),),
            (),
            -5000,
            "USD",
        )

    service = CoreUiProjectionService(
        token,
        blocked_snapshot,
        lambda: None,
        lambda: False,
        lambda: None,
    )
    service.start()
    client = UiIpcClient.connect(service.port, token)
    try:
        ack = client.resume()
        assert ack.accepted is False
        assert ack.reason_code == "HG_DAILY_STOP_REACHED"
        assert client.projection().safe_stop_active is True
    finally:
        client.close()
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
