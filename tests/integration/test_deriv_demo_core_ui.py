from __future__ import annotations

import secrets
from pathlib import Path

from apps.core.lifecycle_service import CoreLifecycleService
from apps.ui.ipc_client import UiIpcClient
from packages.security import SecretValue


def test_core_ui_projects_authorized_fake_demo_balance_and_trusted_clock(
    tmp_path: Path,
) -> None:
    token = SecretValue.from_text(secrets.token_hex(32))
    service = CoreLifecycleService(
        tmp_path,
        ("simulated", "deriv_read_only"),
        force_auth_simulation=True,
        ui_session_token=token,
        deriv_transport="fake-demo",
    )
    service.start()
    client = UiIpcClient.connect(service.ui_port, token)
    try:
        deriv = next(card for card in client.projection().broker_cards if card.broker == "DERIV")

        assert deriv.is_connected
        assert deriv.account_mode.value == "PRACTICE"
        assert deriv.connection_label == "FAKE SIMULADO"
        assert deriv.balance_minor_units == 1_000_000
        assert deriv.currency == "USD"
        assert deriv.clock_synced
        assert deriv.clock_latency_ms is not None
    finally:
        client.close()
        service.emergency_shutdown()
