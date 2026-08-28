from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from apps.core.diagnostic_service import CoreDiagnosticService
from apps.core.health import HealthGate
from apps.core.risk import (
    GlobalRiskConfig,
    RestoredExposure,
    RiskLedger,
    StaticActiveExposurePort,
)
from apps.core.ui_service import CoreUiProjectionService
from apps.ui.ipc_client import UiIpcClient
from packages.domain.models import Broker, Money
from packages.observability.events import InMemoryEventSink
from packages.protocol.ui_messages import (
    BrokerCardStatus,
    HealthGateStatus,
    UiAccountMode,
    UiGlobalState,
    UiProjectionSnapshot,
)
from packages.security import SecretValue


class DummyRuntime:
    def __init__(self, profile_dir: Path) -> None:
        self.profile_directory = profile_dir
        self.database_path = profile_dir / "state.db"
        self.event_sink = InMemoryEventSink()
        self.health_gate = HealthGate()
        self.risk_ledger = RiskLedger(
            GlobalRiskConfig(),
            active_exposure_port=StaticActiveExposurePort(
                (
                    RestoredExposure(
                        "res_1",
                        Broker.DERIV.value,
                        "VRTC1001",
                        Money(1500, "USD"),
                        "frxEURUSD",
                    ),
                )
            ),
        )
        self.worker_supervisor = None


def test_ui_generate_diagnostic_end_to_end(tmp_path: Path) -> None:
    runtime = DummyRuntime(tmp_path)
    # Emit some events to the sink
    runtime.event_sink.emit("CORE_STARTUP", reason_code="BOOT_OK", component="CORE")
    runtime.event_sink.emit("HEALTH_GATE_OPENED", reason_code="ALL_READY")

    diag_service = CoreDiagnosticService(runtime, reports_dir=tmp_path / "diagnostics")

    session_token = SecretValue.from_text(uuid4().hex + uuid4().hex)

    def _snapshot_provider() -> UiProjectionSnapshot:
        return UiProjectionSnapshot(
            global_state=UiGlobalState.READY,
            safe_stop_active=False,
            health_gates=(HealthGateStatus("GLOBAL_ENTRY_GATE", True, None, "Operacional"),),
            broker_cards=(
                BrokerCardStatus(
                    broker="DERIV",
                    account_mode=UiAccountMode.DEMO_READ_ONLY,
                    is_connected=True,
                    connection_label="DEMO",
                    balance_minor_units=10000,
                    currency="USD",
                    clock_synced=True,
                    clock_latency_ms=10,
                ),
            ),
            active_orders=(),
            daily_pnl_minor_units=0,
            daily_pnl_currency="USD",
        )

    ui_service = CoreUiProjectionService(
        session_token=session_token,
        snapshot_provider=_snapshot_provider,
        safe_stop=lambda: None,
        resume=lambda: True,
        shutdown_requested=lambda: None,
        diagnostic_provider=diag_service.generate_bundle,
        request_timeout=5.0,
    )
    ui_service.start()

    try:
        # Connect UI IPC client
        client = UiIpcClient.connect(
            ui_service.port,
            session_token,
            connect_timeout=5.0,
            request_timeout=5.0,
        )

        # Trigger diagnostic generation via UI IPC client
        response = client.generate_diagnostic()

        assert response.success is True
        assert response.reason_code is None
        assert response.bundle_path is not None
        assert response.sha256_hash is not None
        assert response.file_size_bytes > 0

        bundle_path = Path(response.bundle_path)
        assert bundle_path.exists()

        # Check sha256
        actual_sha = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        assert response.sha256_hash == actual_sha
        assert response.file_size_bytes == bundle_path.stat().st_size

        client.close()
    finally:
        ui_service.stop()
