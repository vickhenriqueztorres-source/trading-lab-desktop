from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from apps.core.ui_service import CoreUiProjectionService
from apps.ui.ipc_client import UiIpcClient, UiIpcUnavailable
from packages.domain.models import OrderState
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


def test_ui_ipc_auth_flow_contract() -> None:
    token = SecretValue.from_text(secrets.token_hex(32))
    mock_auth = MagicMock()
    mock_auth.status.return_value = MagicMock(
        auth_state="AUTHORIZED",
        user_id_preview="ju***@gmail.com",
        device_id="dev-12345",
        lease_active=True,
    )
    mock_auth.authorization.return_value = MagicMock(
        new_entries_allowed=True,
        reason=MagicMock(value="OK"),
        expires_at=datetime(2026, 10, 15, 0, 0, 0, tzinfo=UTC),
    )
    mock_auth.start_login.return_value = MagicMock(challenge_id="chal-abc")
    mock_auth.submit_otp.return_value = MagicMock(user_id_preview="ju***@gmail.com")
    mock_auth.activate_product_key.return_value = MagicMock(
        user_id_preview="ju***@gmail.com",
        status=MagicMock(value="AUTHORIZED"),
    )

    service = CoreUiProjectionService(
        token,
        _snapshot,
        lambda: None,
        lambda: True,
        lambda: None,
        auth_supervisor=mock_auth,
    )
    service.start()
    try:
        client = UiIpcClient.connect(service.port, token)
        try:
            # 1. auth_status
            status_resp = client.auth_status()
            assert status_resp.authorized is True
            assert status_resp.status == "AUTHORIZED"
            assert status_resp.user_id_preview == "ju***@gmail.com"
            assert status_resp.device_id == "dev-12345"

            # 2. auth_activate_key
            activate_ack = client.auth_activate_key("TLKEY-PRO-test1234")
            assert activate_ack.ok is True
            assert activate_ack.status == "AUTHORIZED"
            assert activate_ack.user_id_preview == "ju***@gmail.com"

            # 3. auth_start_login
            login_ack = client.auth_start_login("julio@example.com")
            assert login_ack.status == "PENDING_OTP"
            assert login_ack.challenge_id == "chal-abc"
            assert login_ack.user_id_preview == "ju***@example.com"

            # 4. auth_submit_otp
            otp_ack = client.auth_submit_otp("chal-abc", "123456")
            assert otp_ack.status == "AUTHORIZED"
            assert otp_ack.user_id_preview == "ju***@gmail.com"

            # 5. auth_sign_out
            sign_out_ack = client.auth_sign_out()
            assert sign_out_ack.ok is True
        finally:
            client.close()
    finally:
        service.stop()


def test_ui_ipc_resolve_order_command() -> None:
    token = SecretValue.from_text(secrets.token_hex(32))

    # 1. Test graceful rejection when runtime is unavailable
    service = CoreUiProjectionService(
        token,
        _snapshot,
        lambda: None,
        lambda: True,
        lambda: None,
        runtime=None,
    )
    service.start()
    try:
        client = UiIpcClient.connect(service.port, token)
        try:
            ack = client.resolve_order("fake-ord-1", "SETTLE", broker_order_id="123")
            assert ack.accepted is False
            assert ack.reason_code == "CORE_RUNTIME_UNAVAILABLE"
        finally:
            client.close()
    finally:
        service.stop()

    # 2. Test successful resolve_order when runtime is provided
    mock_runtime = MagicMock()
    mock_writer = MagicMock()
    mock_reader = MagicMock()
    mock_reader.one.return_value = {
        "order_id": "test-ord-1",
        "state": OrderState.MANUAL_REVIEW.value,
        "version": 2,
    }
    mock_reader.list_by_state.return_value = []
    mock_reader.list_manual_review_orders.return_value = []
    mock_runtime.writer = mock_writer
    mock_runtime.reader = mock_reader
    mock_runtime.risk_ledger = MagicMock()
    mock_runtime.health_gate = MagicMock()

    service2 = CoreUiProjectionService(
        token,
        _snapshot,
        lambda: None,
        lambda: True,
        lambda: None,
        runtime=mock_runtime,
    )
    service2.start()
    try:
        client = UiIpcClient.connect(service2.port, token)
        try:
            ack_settle = client.resolve_order(
                "test-ord-1",
                "SETTLE",
                broker_order_id="1489201948",
                realized_pnl_minor_units=1500,
                operator="UI_OPERATOR",
                reason="Confirmed WIN",
            )
            assert ack_settle.accepted is True
            assert ack_settle.new_state == "SETTLED"

            ack_reject = client.resolve_order(
                "test-ord-1",
                "REJECT",
                operator="UI_OPERATOR",
                reason="Confirmed never opened",
            )
            assert ack_reject.accepted is True
            assert ack_reject.new_state == "REJECTED"
        finally:
            client.close()
    finally:
        service2.stop()
