from __future__ import annotations

from apps.core.observability.slo import SLOConfig, SLOMonitor, SLOSeverity


def test_slo_burn_rate_budget_and_severity() -> None:
    monitor = SLOMonitor(
        [SLOConfig("availability", 0.99, warning_burn_rate=2, critical_burn_rate=5)]
    )
    for _ in range(99):
        monitor.record("availability", good=True)
    monitor.record("availability", good=False)
    status = monitor.status("availability")
    assert status.error_rate == 0.01
    assert status.burn_rate == 1.0
    assert status.remaining_budget == 0
    assert status.projected_breach
    assert status.severity is SLOSeverity.HEALTHY


def test_latency_observation_uses_declared_units() -> None:
    monitor = SLOMonitor([SLOConfig("ack_ms", 0.95, threshold=500)])
    monitor.observe_latency("ack_ms", 0.4)
    monitor.observe_latency("ack_ms", 0.6)
    assert monitor.status("ack_ms").bad_events == 1
