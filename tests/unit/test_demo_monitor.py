from decimal import Decimal

from apps.core.observability.demo_monitor import DemoMonitor


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_demo_monitor_tracks_bounded_redacted_practice_evidence() -> None:
    clock = Clock()
    monitor = DemoMonitor(clock=clock, max_alerts=3)
    monitor.start()
    clock.now += 2.5
    monitor.record_health(trading_ready=True)
    monitor.record_order("ACCEPTED", latency_seconds=0.2)
    monitor.record_order("SETTLED")
    monitor.record_reconciliation(duration_seconds=0.4, divergence=False)
    monitor.set_lease_remaining(30.0)
    monitor.record_realized_pnl(Decimal("0.82"))

    status = monitor.get_status()
    assert status.running is True
    assert status.uptime_seconds == 2.5
    assert status.orders_total == 3
    assert dict(status.orders_by_state) == {
        "ACCEPTED": 1,
        "RECONCILING": 1,
        "SETTLED": 1,
    }
    assert status.reconciliations_total == 1
    assert status.divergences_total == 0
    assert status.realized_pnl == Decimal("0.82")
    assert status.health_ready is True
    assert status.alerts == ()


def test_demo_monitor_alerts_on_unknown_divergence_and_fencing() -> None:
    monitor = DemoMonitor(max_alerts=3)
    monitor.start()
    monitor.record_order("UNKNOWN")
    monitor.record_reconciliation(duration_seconds=1.0, divergence=True)
    monitor.record_fencing_rejection()
    monitor.record_duplicate_submission()

    status = monitor.get_status()
    assert status.divergences_total == 1
    assert status.fencing_rejections_total == 1
    assert status.alerts == (
        "IQOPTION_RECONCILIATION_DIVERGENCE",
        "IQOPTION_FENCING_REJECTED",
        "IQOPTION_DUPLICATE_SUBMISSION",
    )
