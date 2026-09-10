from __future__ import annotations

import secrets
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from apps.core.execution_state import TransportSupervisor
from apps.core.lifecycle_service import CoreLifecycleService, CoreServiceState
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSpec
from apps.core.worker_supervisor import WorkerHealthState
from apps.ui.ipc_client import UiIpcClient
from packages.domain.market import BrokerAccountBalance, BrokerClockSnapshot
from packages.domain.models import Broker
from packages.observability.events import InMemoryEventSink
from packages.protocol import ProtocolError, ProtocolErrorCode, UiAccountMode
from packages.security import SecretValue


@pytest.mark.parametrize(
    ("mode", "connection_mode", "account_type", "ui_mode", "reason_code"),
    [
        (
            "practice",
            "DEMO_AUTH_FINANCIAL",
            "DEMO",
            UiAccountMode.PRACTICE,
            "IQOPTION_PRACTICE_CONNECTED",
        ),
        (
            "real",
            "REAL_AUTH_READ_ONLY",
            "REAL",
            UiAccountMode.REAL,
            "IQOPTION_REAL_READ_ONLY_CONNECTED",
        ),
    ],
)
def test_iqoption_connection_projects_verified_balance_and_never_enables_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    connection_mode: str,
    account_type: str,
    ui_mode: UiAccountMode,
    reason_code: str,
) -> None:
    captured_specs: list[ReadOnlyWorkerSpec] = []

    class FakeClient:
        capabilities = SimpleNamespace(
            connection_mode=connection_mode,
            can_submit_orders=mode == "practice",
            supports_market_data=mode == "practice",
            supports_reconciliation=mode == "practice",
            supports_order_events=mode == "practice",
        )

        @staticmethod
        def next_order_event(_timeout: float = 0.0) -> None:
            return None

        @staticmethod
        def broker_balance() -> BrokerAccountBalance:
            return BrokerAccountBalance(12345, "USD", account_type, datetime.now(UTC))

        @staticmethod
        def broker_clock() -> BrokerClockSnapshot:
            now = datetime.now(UTC)
            return BrokerClockSnapshot(
                int(now.timestamp()),
                now,
                0.01,
                Decimal("0"),
            )

    class FakeSupervisor:
        def __init__(
            self,
            _health_gate: object,
            spec: ReadOnlyWorkerSpec,
            **_kwargs: object,
        ) -> None:
            captured_specs.append(spec)
            self.client = FakeClient()
            self.health_state = WorkerHealthState.STOPPED
            self.process = None

        def start(self) -> FakeClient:
            self.health_state = WorkerHealthState.READY
            return self.client

        def shutdown(self, _grace_seconds: float) -> None:
            self.health_state = WorkerHealthState.STOPPED

    monkeypatch.setattr("apps.core.lifecycle_service.ReadOnlyWorkerSupervisor", FakeSupervisor)
    token = SecretValue.from_text(secrets.token_hex(32))
    service = CoreLifecycleService(
        tmp_path,
        ("simulated",),
        force_auth_simulation=True,
        ui_session_token=token,
    )
    service.start()
    try:
        # This fixture implements account reads only, not market-history. The
        # real trader correctly treats that missing method as a session fault.
        monkeypatch.setattr(service._iqoption_auto_trader, "start", lambda: None)
        accepted, connected, reason = service.connect_iqoption_selected_account(mode)
        assert (accepted, connected, reason) == (True, True, reason_code)
        assert captured_specs
        assert captured_specs[-1].allow_demo_financial_submission is (mode == "practice")
        assert captured_specs[-1].allow_real_financial_submission is False

        client = UiIpcClient.connect(service.ui_port, token)
        try:
            card = next(
                item for item in client.projection().broker_cards if item.broker == "IQOPTION"
            )
        finally:
            client.close()
        assert card.is_connected is True
        assert card.account_mode is ui_mode
        assert card.balance_minor_units == 12345
        assert card.currency == "USD"
    finally:
        service.emergency_shutdown()


def test_iqoption_connection_preserves_worker_error_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSupervisor:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise ProtocolError(
                ProtocolErrorCode.IQOPTION_AUTH_FAILED,
                "worker rejected credentials",
            )

        def shutdown(self, _grace_seconds: float) -> None:
            pass

    monkeypatch.setattr(
        "apps.core.lifecycle_service.ReadOnlyWorkerSupervisor",
        FailingSupervisor,
    )
    service = CoreLifecycleService(
        tmp_path,
        ("simulated",),
        force_auth_simulation=True,
        ui_session_token=SecretValue.from_text(secrets.token_hex(32)),
    )
    service.start()
    try:
        accepted, connected, reason = service.connect_iqoption_selected_account("practice")
    finally:
        service.emergency_shutdown()

    assert (accepted, connected, reason) == (False, False, "IQOPTION_AUTH_FAILED")


def test_saved_practice_credentials_reconnect_without_exposing_password_to_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_modes: list[str] = []

    class FakeVault:
        def __init__(self, _directory: Path) -> None:
            pass

        @staticmethod
        def configured_account_mode() -> str:
            return "practice"

    class FakeClient:
        capabilities = SimpleNamespace(
            connection_mode="DEMO_AUTH_FINANCIAL",
            can_submit_orders=True,
            supports_market_data=True,
            supports_reconciliation=True,
            supports_order_events=True,
        )

        @staticmethod
        def next_order_event(_timeout: float = 0.0) -> None:
            return None

        @staticmethod
        def broker_balance() -> BrokerAccountBalance:
            return BrokerAccountBalance(50000, "USD", "DEMO", datetime.now(UTC))

        @staticmethod
        def broker_clock() -> BrokerClockSnapshot:
            now = datetime.now(UTC)
            return BrokerClockSnapshot(int(now.timestamp()), now, 0.01, Decimal("0"))

    class FakeSupervisor:
        def __init__(
            self,
            _health_gate: object,
            spec: ReadOnlyWorkerSpec,
            **_kwargs: object,
        ) -> None:
            captured_modes.append(spec.extra_arguments[-1])
            self.client = FakeClient()
            self.health_state = WorkerHealthState.STOPPED
            self.process = None

        def start(self) -> FakeClient:
            self.health_state = WorkerHealthState.READY
            return self.client

        def shutdown(self, _grace_seconds: float) -> None:
            self.health_state = WorkerHealthState.STOPPED

    monkeypatch.setattr("apps.core.lifecycle_service.IQOptionCredentialVault", FakeVault)
    monkeypatch.setattr("apps.core.lifecycle_service.ReadOnlyWorkerSupervisor", FakeSupervisor)
    service = CoreLifecycleService(tmp_path, ("simulated",), force_auth_simulation=True)
    service.start()
    try:
        result = service.connect_iqoption_selected_account("saved")
    finally:
        service.emergency_shutdown()

    assert result == (True, True, "IQOPTION_PRACTICE_CONNECTED")
    assert captured_modes == ["practice"]


def test_saved_real_credentials_are_never_selected_automatically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeVault:
        def __init__(self, _directory: Path) -> None:
            pass

        @staticmethod
        def configured_account_mode() -> str:
            return "real"

    monkeypatch.setattr("apps.core.lifecycle_service.IQOptionCredentialVault", FakeVault)
    service = CoreLifecycleService(tmp_path, ("simulated",), force_auth_simulation=True)

    assert service.connect_iqoption_selected_account("saved") == (
        False,
        False,
        "IQOPTION_SAVED_REAL_REQUIRES_CONFIRMATION",
    )


def test_manual_iqoption_connection_returns_busy_during_existing_recovery(
    tmp_path: Path,
) -> None:
    service = CoreLifecycleService(tmp_path, ("simulated",), force_auth_simulation=True)
    service._iqoption_connecting = SimpleNamespace()

    assert service.connect_iqoption_selected_account("practice") == (
        False,
        False,
        "IQOPTION_CONNECTION_IN_PROGRESS",
    )


def test_pending_practice_order_starts_saved_recovery_without_ui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeVault:
        def __init__(self, _directory: Path) -> None:
            pass

        @staticmethod
        def configured_account_mode() -> str:
            return "practice"

    monkeypatch.setattr("apps.core.lifecycle_service.IQOptionCredentialVault", FakeVault)
    service = CoreLifecycleService(tmp_path, ("simulated",), force_auth_simulation=True)

    def connect_saved(mode: str) -> tuple[bool, bool, str]:
        calls.append(mode)
        service._iqoption_session_invalidated = False
        return True, True, "IQOPTION_PRACTICE_CONNECTED"

    monkeypatch.setattr(service, "connect_iqoption_selected_account", connect_saved)
    service.start()
    try:
        service._schedule_saved_iqoption_recovery(has_iqoption_recovery=True)
        thread = service._iqoption_startup_recovery_thread
        assert thread is not None
        thread.join(timeout=2.0)
    finally:
        service.emergency_shutdown()

    assert calls == ["saved"]


def test_saved_recovery_continues_after_bounded_round_while_armed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeVault:
        def __init__(self, _directory: Path) -> None:
            pass

        @staticmethod
        def configured_account_mode() -> str:
            return "practice"

    monkeypatch.setattr("apps.core.lifecycle_service.IQOptionCredentialVault", FakeVault)
    monkeypatch.setattr(
        "apps.core.lifecycle_service._IQOPTION_RECOVERY_DELAYS_SECONDS",
        (0.0, 0.0, 0.0, 0.0, 0.0),
    )
    service = CoreLifecycleService(tmp_path, ("simulated",), force_auth_simulation=True)
    service._transport_supervisor.arm(transport_available=False)

    def connect_saved(mode: str) -> tuple[bool, bool, str]:
        calls.append(mode)
        if len(calls) == 6:
            service._iqoption_session_invalidated = False
            return True, True, "IQOPTION_PRACTICE_CONNECTED"
        return False, False, "IQOPTION_NETWORK_UNREACHABLE"

    monkeypatch.setattr(service, "connect_iqoption_selected_account", connect_saved)
    service.start()
    try:
        service._schedule_saved_iqoption_recovery(has_iqoption_recovery=True)
        thread = service._iqoption_startup_recovery_thread
        assert thread is not None
        thread.join(timeout=2.0)
        assert not thread.is_alive()
    finally:
        service.emergency_shutdown()

    assert calls == ["saved"] * 6


def test_armed_recovery_waits_out_quarantine_then_reconnects(monkeypatch) -> None:
    class FakeStop:
        def __init__(self) -> None:
            self.waits: list[float] = []

        @staticmethod
        def is_set() -> bool:
            return False

        def wait(self, seconds: float) -> bool:
            self.waits.append(seconds)
            return False

    service = CoreLifecycleService.__new__(CoreLifecycleService)
    service._state = CoreServiceState.READY
    service._transport_supervisor = TransportSupervisor(initially_armed=True)
    service._iqoption_bot_reason = "TRANSPORT_DOWN"
    service._iqoption_session_invalidated = True
    service._iqoption_recovery_stop = FakeStop()
    service._runtime = SimpleNamespace(
        event_sink=InMemoryEventSink(),
        reader=SimpleNamespace(list_nonterminal_orders=lambda: []),
    )
    service._iqoption_connection_safety = SimpleNamespace(
        snapshot=MagicMock(
            side_effect=(
                SimpleNamespace(quarantine_active=True, retry_after_seconds=341),
                SimpleNamespace(quarantine_active=False, retry_after_seconds=0),
            )
        )
    )
    calls: list[str] = []

    def connect_saved(mode: str) -> tuple[bool, bool, str]:
        calls.append(mode)
        service._iqoption_session_invalidated = False
        return True, True, "IQOPTION_PRACTICE_CONNECTED"

    monkeypatch.setattr(service, "connect_iqoption_selected_account", connect_saved)
    monkeypatch.setattr("apps.core.lifecycle_service._IQOPTION_RECOVERY_DELAYS_SECONDS", (0.0,))

    service._iqoption_recovery_loop()

    assert service._iqoption_recovery_stop.waits == [341, 0.0]
    assert calls == ["saved"]
    assert service._iqoption_bot_reason == "IQOPTION_BOT_ARMED"
    assert any(
        event.event_name == "iqoption_recovery_waiting"
        and event.reason_code == "IQOPTION_CONNECTION_QUARANTINED"
        for event in service._runtime.event_sink.events
    )


def test_iq_recovery_preserves_deriv_entry_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.core.lifecycle_service._IQOPTION_RECOVERY_DELAYS_SECONDS",
        (0.0, 0.0, 0.0, 0.0, 0.0),
    )
    service = CoreLifecycleService(tmp_path, ("simulated",), force_auth_simulation=True)
    service.start()
    try:
        runtime = service._require_runtime()
        service._deriv_account_id = "deriv-demo"
        runtime.stop_new_entries_for(Broker.DERIV, "deriv-demo")
        assert runtime.resume_new_entries_for(Broker.DERIV, "deriv-demo") is True
        service._safe_stop = False
        service._transport_supervisor.arm()
        service._iqoption_bot_armed = True

        def reconnect(_mode: str) -> tuple[bool, bool, str]:
            service._iqoption_session_invalidated = False
            service._iqoption_auto_trader.on_transport_up()
            return True, True, "IQOPTION_PRACTICE_CONNECTED"

        monkeypatch.setattr(service, "connect_iqoption_selected_account", reconnect)

        service._request_iqoption_recovery("IQOPTION_BROKER_SESSION_UNAVAILABLE")
        thread = service._iqoption_startup_recovery_thread
        assert thread is not None
        thread.join(timeout=2.0)

        assert runtime.health_gate.state_for("DERIV", "deriv-demo").is_open is True
        assert service._safe_stop is False
        assert service._iqoption_bot_armed is True
        assert service._iqoption_bot_reason == "IQOPTION_BOT_ARMED"
    finally:
        service.emergency_shutdown()


def test_core_connection_guard_blocks_fourth_external_session_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    starts = 0

    class FailingSupervisor:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.client = None

        def start(self) -> None:
            nonlocal starts
            starts += 1
            raise ProtocolError(
                ProtocolErrorCode.IQOPTION_NETWORK_UNREACHABLE,
                "network unavailable",
            )

        def shutdown(self, _grace_seconds: float) -> None:
            return None

    monkeypatch.setattr(
        "apps.core.lifecycle_service.ReadOnlyWorkerSupervisor",
        FailingSupervisor,
    )
    service = CoreLifecycleService(tmp_path, ("simulated",), force_auth_simulation=True)
    service.start()
    try:
        results = [service.connect_iqoption_selected_account("practice") for _ in range(4)]
    finally:
        service.emergency_shutdown()

    assert [item[2] for item in results[:3]] == ["IQOPTION_NETWORK_UNREACHABLE"] * 3
    assert results[3] == (False, False, "IQOPTION_CONNECTION_QUARANTINED")
    assert starts == 3


def test_iqoption_connector_cannot_attach_after_shutdown_begins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutdown_calls: list[float] = []
    service: CoreLifecycleService

    class FakeSupervisor:
        health_state = WorkerHealthState.STOPPED
        process = None
        client = SimpleNamespace(
            capabilities=SimpleNamespace(connection_mode="DEMO_AUTH_FINANCIAL")
        )

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self) -> object:
            service._iqoption_recovery_stop.set()
            self.health_state = WorkerHealthState.READY
            return self.client

        def shutdown(self, grace_seconds: float) -> None:
            shutdown_calls.append(grace_seconds)
            self.health_state = WorkerHealthState.STOPPED

    monkeypatch.setattr("apps.core.lifecycle_service.ReadOnlyWorkerSupervisor", FakeSupervisor)
    service = CoreLifecycleService(tmp_path, ("simulated",), force_auth_simulation=True)
    service.start()
    try:
        result = service.connect_iqoption_selected_account("practice")
    finally:
        service.emergency_shutdown()

    assert result == (False, False, "LIFECYCLE_STOPPING")
    assert 0.2 in shutdown_calls
    assert service._iqoption is None
