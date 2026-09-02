from __future__ import annotations

import secrets
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.core.lifecycle_service import CoreLifecycleService
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSpec
from apps.core.worker_supervisor import WorkerHealthState
from apps.ui.ipc_client import UiIpcClient
from packages.domain.market import BrokerAccountBalance, BrokerClockSnapshot
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
    monkeypatch.setattr(
        service,
        "connect_iqoption_selected_account",
        lambda mode: (calls.append(mode) is None, True, "IQOPTION_PRACTICE_CONNECTED"),
    )
    service.start()
    try:
        service._schedule_saved_iqoption_recovery(has_iqoption_recovery=True)
        thread = service._iqoption_startup_recovery_thread
        assert thread is not None
        thread.join(timeout=2.0)
    finally:
        service.emergency_shutdown()

    assert calls == ["saved"]


def test_saved_recovery_stops_after_five_bounded_attempts(
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
    monkeypatch.setattr(
        service,
        "connect_iqoption_selected_account",
        lambda mode: (
            False,
            calls.append(mode) is not None,
            "IQOPTION_NETWORK_UNREACHABLE",
        ),
    )
    service.start()
    try:
        service._schedule_saved_iqoption_recovery(has_iqoption_recovery=True)
        thread = service._iqoption_startup_recovery_thread
        assert thread is not None
        thread.join(timeout=2.0)
        assert not thread.is_alive()
    finally:
        service.emergency_shutdown()

    assert calls == ["saved"] * 5


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
