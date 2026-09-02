from __future__ import annotations

import secrets
from pathlib import Path

import pytest

from apps.core.ui_service import CoreUiProjectionService
from apps.ui.ipc_client import UiIpcClient
from packages.brokers.iqoption import credentials as credential_module
from packages.brokers.iqoption.credentials import IQOptionCredentials, IQOptionCredentialVault
from packages.protocol import (
    BrokerCardStatus,
    HealthGateStatus,
    UiAccountMode,
    UiGlobalState,
    UiIqOptionLoginAck,
    UiProjectionSnapshot,
)
from packages.security import SecretValue


def test_iqoption_credentials_support_explicit_modes_and_are_redacted() -> None:
    ephemeral_secret = secrets.token_urlsafe(18)
    credentials = IQOptionCredentials("Trader@Example.com", SecretValue.from_text(ephemeral_secret))
    assert credentials.email == "trader@example.com"
    assert credentials.account_mode == "practice"
    assert ephemeral_secret not in repr(credentials)

    real_credentials = IQOptionCredentials(
        "trader@example.com",
        SecretValue.from_text(ephemeral_secret),
        account_mode="real",
    )
    assert real_credentials.account_mode == "real"

    with pytest.raises(ValueError, match="mode"):
        IQOptionCredentials(
            "trader@example.com",
            SecretValue.from_text(ephemeral_secret),
            account_mode="unsupported",
        )


def test_iqoption_login_ack_rejects_inconsistent_connected_state() -> None:
    with pytest.raises(ValueError):
        UiIqOptionLoginAck(False, True, "BAD")


def test_iqoption_vault_round_trip_is_scoped_to_practice(monkeypatch: pytest.MonkeyPatch) -> None:
    values: dict[str, SecretValue] = {}

    class FakeVault:
        def __init__(self, _directory: Path) -> None:
            pass

        def set_secret(self, key: str, value: SecretValue) -> None:
            values[key] = value

        def get_secret(self, key: str) -> SecretValue | None:
            return values.get(key)

        def delete_secret(self, key: str) -> bool:
            return values.pop(key, None) is not None

    monkeypatch.setattr(credential_module, "WindowsUserScopedVault", FakeVault)
    vault = IQOptionCredentialVault(Path("unused"))
    ephemeral_secret = secrets.token_urlsafe(18)
    vault.save(IQOptionCredentials("trader@example.com", SecretValue.from_text(ephemeral_secret)))

    loaded = vault.load()
    assert loaded is not None
    assert loaded.email == "trader@example.com"
    assert loaded.password.reveal_text() == ephemeral_secret
    assert loaded.account_mode == "practice"
    assert vault.configured_account_mode() == "practice"

    vault.clear()
    assert vault.load() is None
    assert vault.configured_account_mode() is None


def test_iqoption_login_command_accepts_saved_reconnect_without_credentials() -> None:
    from packages.protocol import UiIqOptionLoginCommand

    command = UiIqOptionLoginCommand("saved")

    assert command.to_payload() == {"account_mode": "saved"}


def test_iqoption_login_uses_authenticated_ui_channel_without_broker_secret() -> None:
    token = SecretValue.from_text(secrets.token_hex(32))
    calls: list[str] = []

    def login(account_mode: str) -> tuple[bool, bool, str]:
        calls.append(account_mode)
        return True, True, "IQOPTION_PRACTICE_CONNECTED"

    def snapshot() -> UiProjectionSnapshot:
        return UiProjectionSnapshot(
            global_state=UiGlobalState.READY,
            safe_stop_active=True,
            health_gates=(HealthGateStatus("GLOBAL", True, None, "ok"),),
            broker_cards=(
                BrokerCardStatus(
                    "IQOPTION",
                    UiAccountMode.PRACTICE,
                    False,
                    None,
                    None,
                    False,
                ),
            ),
            active_orders=(),
            daily_pnl_minor_units=0,
            daily_pnl_currency=None,
            global_exposure_minor_units=0,
            global_max_exposure_minor_units=0,
            consecutive_losses=0,
            risk_state="NORMAL",
            digit_risk_config=None,
            cooldown_remaining_seconds=0,
            digit_frequency=None,
        )

    service = CoreUiProjectionService(
        token,
        snapshot,
        lambda: None,
        lambda: True,
        lambda: None,
        iqoption_login=login,
    )
    service.start()
    try:
        client = UiIpcClient.connect(service.port, token)
        try:
            ack = client.login_iqoption("practice")
        finally:
            client.close()
    finally:
        service.stop()
    assert ack.accepted is True
    assert ack.connected is True
    assert calls == ["practice"]
