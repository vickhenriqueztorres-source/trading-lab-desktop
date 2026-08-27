from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from apps.core.deriv_telemetry import DerivTelemetrySnapshot, DerivTelemetrySource
from apps.core.health import HealthGate
from apps.core.ui_service import CoreUiProjectionBuilder
from apps.core.worker_supervisor import WorkerHealthState
from packages.domain.market import BrokerClockSnapshot
from packages.market_data import DigitFrequencySnapshot
from packages.observability.events import PersistentJsonlEventSink


class _Reader:
    def list_reconciliation_candidates(self) -> list[dict[str, object]]:
        return []

    def list_nonterminal_orders(self) -> list[dict[str, object]]:
        return []


def _telemetry() -> DerivTelemetrySnapshot:
    return DerivTelemetrySnapshot(
        DerivTelemetrySource.DEMO_LIVE,
        "DEMO",
        True,
        None,
        BrokerClockSnapshot(
            1_900_000_000,
            datetime(2030, 3, 17, tzinfo=UTC),
            0.01,
            Decimal("0.1"),
        ),
        None,
        DigitFrequencySnapshot(
            "R_100",
            500,
            (50, 50, 50, 50, 50, 50, 50, 50, 50, 50),
            tuple(Decimal("10") for _ in range(10)),
            0,
        ),
    )


def test_ready_to_arm_is_distinct_from_armed_and_ready_to_trade() -> None:
    gate = HealthGate()
    gate.block("HG_SAFE_STOP")
    runtime = SimpleNamespace(
        health_gate=gate,
        reader=_Reader(),
        safe_stop_active=True,
        dispatcher_started=False,
    )
    builder = CoreUiProjectionBuilder(
        runtime,  # type: ignore[arg-type]
        deriv_health=lambda: WorkerHealthState.READY,
        deriv_telemetry=_telemetry,
    )

    disarmed = builder.trading_readiness()
    assert disarmed.core_available is True
    assert disarmed.ready_to_arm is True
    assert disarmed.armed is False
    assert disarmed.ready_to_trade is False

    gate.clear("HG_SAFE_STOP")
    runtime.safe_stop_active = False
    runtime.dispatcher_started = True
    armed = builder.trading_readiness()
    assert armed.ready_to_arm is True
    assert armed.armed is True
    assert armed.ready_to_trade is True


def test_operational_journal_persists_redacted_lifecycle_and_rotates(tmp_path: Path) -> None:
    path = tmp_path / "operational-journal.jsonl"
    sink = PersistentJsonlEventSink(path, max_bytes=220)
    sink.emit("worker_started", process_id=123, worker_type="SIMULATED")
    sink.emit("health_gate_blocked", reason_code="HG_SAFE_STOP")
    sink.emit("recovery_completed", generation=2, armed=False)

    assert path.exists()
    assert path.with_suffix(".jsonl.1").exists()
    current = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert current[-1]["event"] == "recovery_completed"
    assert current[-1]["fields"] == {"armed": False, "generation": 2}
