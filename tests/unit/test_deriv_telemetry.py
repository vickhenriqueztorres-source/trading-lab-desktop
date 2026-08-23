from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from apps.core.deriv_telemetry import DerivTelemetryMonitor, DerivTelemetrySource
from apps.core.health import HealthGate
from apps.core.read_only_worker_supervisor import ReadOnlyWorkerSupervisor
from packages.domain.market import BrokerClockSnapshot


class _Capabilities:
    connection_mode = "PUBLIC_READ_ONLY"


class _ClockClient:
    capabilities = _Capabilities()

    def __init__(self, clock: BrokerClockSnapshot) -> None:
        self.clock = clock

    def broker_clock(self) -> BrokerClockSnapshot:
        return self.clock


class _Supervisor:
    def __init__(self, client: _ClockClient) -> None:
        self.client = client


@pytest.mark.parametrize(
    "round_trip,offset",
    [(1.001, Decimal("0")), (0.010, Decimal("2.001"))],
)
def test_untrusted_deriv_clock_blocks_and_proven_recovery_clears_gate(
    round_trip: float,
    offset: Decimal,
) -> None:
    observed = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    client = _ClockClient(BrokerClockSnapshot(1_700_000_100, observed, round_trip, offset))
    supervisor = cast(ReadOnlyWorkerSupervisor, _Supervisor(client))
    gate = HealthGate()
    monitor = DerivTelemetryMonitor(
        supervisor,
        gate,
        DerivTelemetrySource.PUBLIC_LIVE,
    )

    blocked = monitor.probe_once()
    assert blocked.reason_code == "MD_CLOCK_UNTRUSTED"
    assert gate.contains("MD_CLOCK_UNTRUSTED")

    client.clock = BrokerClockSnapshot(1_700_000_100, observed, 0.010, Decimal("0.100"))
    recovered = monitor.probe_once()
    assert recovered.reason_code is None
    assert not gate.contains("MD_CLOCK_UNTRUSTED")
