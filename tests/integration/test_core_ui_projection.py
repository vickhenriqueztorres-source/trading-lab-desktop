from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.core.lifecycle_service import CoreLifecycleService, CoreServiceState
from apps.core.runtime import CoreRuntime
from apps.core.ui_service import CoreUiProjectionBuilder, CoreUiProjectionService
from apps.simulated_worker.scenarios import WorkerScenario
from apps.ui.ipc_client import UiIpcClient
from packages.domain.models import Broker, Direction, Money, OrderRequest
from packages.protocol import UiGlobalState
from packages.security import SecretValue


def _request(name: str) -> OrderRequest:
    return OrderRequest(
        correlation_id=f"corr-{name}",
        broker=Broker.DERIV,
        account_id="practice-account",
        product="DIGITAL_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1_000, "USD"),
        strategy_id="strategy-test",
        strategy_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )


def test_core_ui_projection_safe_stop_and_resume_preserve_core_authority(
    tmp_path: Path,
) -> None:
    token = SecretValue.from_text(secrets.token_hex(32))
    service = CoreLifecycleService(
        tmp_path,
        ("simulated",),
        force_auth_simulation=True,
        ui_session_token=token,
    )
    service.start()
    client = UiIpcClient.connect(service.ui_port, token)
    try:
        initial = client.projection()
        assert initial.global_state is UiGlobalState.READY
        assert all(card.balance_minor_units is None for card in initial.broker_cards)

        assert client.safe_stop().accepted
        stopped = client.projection()
        assert stopped.safe_stop_active is True
        assert stopped.global_state is UiGlobalState.SAFE_STOPPED
        assert service.state is CoreServiceState.SAFE_STOP

        assert client.resume().accepted
        resumed = client.projection()
        assert resumed.safe_stop_active is False
        assert resumed.global_state is UiGlobalState.READY
        assert service.state is CoreServiceState.READY
    finally:
        client.close()
        service.emergency_shutdown()


def test_ui_safe_stop_blocks_new_intent_but_open_order_still_settles(
    tmp_path: Path,
) -> None:
    runtime = CoreRuntime(tmp_path / "runtime", worker_scenario=WorkerScenario.NORMAL_LIFECYCLE)
    runtime.start()
    token = SecretValue.from_text(secrets.token_hex(32))
    projection = CoreUiProjectionBuilder(runtime, deriv_health=lambda: None)
    ui_service = CoreUiProjectionService(
        token,
        projection.snapshot,
        runtime.stop_new_entries,
        runtime.resume_new_entries,
        lambda: None,
    )
    ui_service.start()
    client = UiIpcClient.connect(ui_service.port, token)
    try:
        persisted = runtime.submit(_request("accepted-before-stop"))
        assert client.safe_stop().accepted
        with pytest.raises(RuntimeError, match="HG_SAFE_STOP"):
            runtime.submit(_request("blocked-after-stop"))
        assert runtime.reader.count("trade_intents") == 1

        deadline = time.monotonic() + 3.0
        order: dict[str, object] | None = None
        while time.monotonic() < deadline:
            order = runtime.reader.one("orders", "order_id", persisted.order_id)
            if order is not None and order["state"] == "SETTLED":
                break
            time.sleep(0.01)
        assert order is not None and order["state"] == "SETTLED"
        assert runtime.reader.financial_effect_counts(persisted.order_id) == {
            "pnl_application_count": 1,
            "reservation_release_count": 1,
        }
    finally:
        client.close()
        ui_service.stop()
        runtime.shutdown()
