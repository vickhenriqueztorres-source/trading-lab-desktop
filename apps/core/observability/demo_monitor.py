"""Bounded in-process monitor for controlled IQ Option Practice validation."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from threading import Lock
from typing import cast

from apps.core.observability.metrics import Metrics
from apps.core.observability.slo import DEFAULT_SLOS, SLOMonitor, SLOStatus


@dataclass(frozen=True, slots=True)
class DemoMonitorStatus:
    running: bool
    uptime_seconds: float
    orders_total: int
    orders_by_state: tuple[tuple[str, int], ...]
    reconciliations_total: int
    divergences_total: int
    fencing_rejections_total: int
    lease_remaining_seconds: float
    realized_pnl: Decimal
    health_ready: bool
    alerts: tuple[str, ...]
    slo_statuses: tuple[SLOStatus, ...]


class DemoMonitor:
    """Collect redacted operational evidence without making trading decisions."""

    def __init__(self, *, clock: object = time.monotonic, max_alerts: int = 100) -> None:
        if max_alerts <= 0:
            raise ValueError("max_alerts must be positive")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock
        self._max_alerts = max_alerts
        self._lock = Lock()
        self._metrics = Metrics()
        self._slos = SLOMonitor(DEFAULT_SLOS, clock=clock)
        self._started_at: float | None = None
        self._alerts: deque[str] = deque(maxlen=max_alerts)
        self._divergences = 0
        self._realized_pnl = Decimal("0")
        self._health_ready = False

    def start(self) -> None:
        with self._lock:
            if self._started_at is None:
                self._started_at = float(self._clock())

    def stop(self) -> None:
        with self._lock:
            self._started_at = None

    def record_health(self, *, trading_ready: bool) -> None:
        self._slos.record("availability_of_trading_ready", good=trading_ready)
        with self._lock:
            self._health_ready = trading_ready
            if not trading_ready:
                self._alerts.append("IQOPTION_PRACTICE_NOT_READY")

    def record_order(self, state: str, *, latency_seconds: float | None = None) -> None:
        normalized = state.strip().upper()
        if not normalized:
            raise ValueError("order state is required")
        self._metrics.record_order(normalized, latency_seconds)
        if latency_seconds is not None:
            self._slos.observe_latency("order_gateway_ack_p95_ms", latency_seconds)
            self._slos.observe_latency("order_gateway_ack_p99_ms", latency_seconds)
        if normalized == "UNKNOWN":
            with self._lock:
                self._alerts.append("IQOPTION_ORDER_UNKNOWN")

    def record_reconciliation(self, *, duration_seconds: float, divergence: bool) -> None:
        if duration_seconds < 0:
            raise ValueError("reconciliation duration cannot be negative")
        self._metrics.record_order("RECONCILING")
        self._slos.observe_latency("reconciliation_p95_seconds", duration_seconds)
        self._slos.observe_latency("reconciliation_p99_seconds", duration_seconds)
        with self._lock:
            if divergence:
                self._divergences += 1
                self._alerts.append("IQOPTION_RECONCILIATION_DIVERGENCE")

    def record_duplicate_submission(self) -> None:
        self._slos.record("duplicate_internal_submission_rate", good=False)
        with self._lock:
            self._alerts.append("IQOPTION_DUPLICATE_SUBMISSION")

    def record_unknown_age(self, *, unresolved_over_15m: bool) -> None:
        self._slos.record(
            "unknown_orders_unresolved_over_15m",
            good=not unresolved_over_15m,
        )

    def record_fencing_rejection(self) -> None:
        self._metrics.record_fencing_rejection()
        with self._lock:
            self._alerts.append("IQOPTION_FENCING_REJECTED")

    def set_lease_remaining(self, seconds: float) -> None:
        self._metrics.set_leader_lease_remaining(seconds)

    def record_realized_pnl(self, amount: Decimal) -> None:
        if not isinstance(amount, Decimal) or not amount.is_finite():
            raise ValueError("realized P&L must be a finite Decimal")
        with self._lock:
            self._realized_pnl += amount

    def get_status(self) -> DemoMonitorStatus:
        snapshot = self._metrics.snapshot()
        with self._lock:
            started = self._started_at
            uptime = 0.0 if started is None else max(0.0, float(self._clock()) - started)
            alerts = tuple(self._alerts)
            divergences = self._divergences
            pnl = self._realized_pnl
            health_ready = self._health_ready
        statuses = self._slos.get_status()
        assert isinstance(statuses, dict)
        orders_total = cast(int, snapshot["orders_total"])
        orders_by_state = cast(dict[str, int], snapshot["orders_by_state"])
        reconciliations_total = cast(int, snapshot["order_reconciliation_total"])
        fencing_rejections_total = cast(int, snapshot["fencing_rejections_total"])
        lease_remaining_seconds = cast(float, snapshot["leader_lease_remaining_seconds"])
        return DemoMonitorStatus(
            running=started is not None,
            uptime_seconds=uptime,
            orders_total=orders_total,
            orders_by_state=tuple(sorted(orders_by_state.items())),
            reconciliations_total=reconciliations_total,
            divergences_total=divergences,
            fencing_rejections_total=fencing_rejections_total,
            lease_remaining_seconds=lease_remaining_seconds,
            realized_pnl=pnl,
            health_ready=health_ready,
            alerts=alerts,
            slo_statuses=tuple(statuses[name] for name in sorted(statuses)),
        )


__all__ = ["DemoMonitor", "DemoMonitorStatus", "main"]


def main() -> int:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="IQ Option Demo Monitor")
    parser.add_argument("--config", default="config/demo_force_config.yaml", help="Config file")
    args = parser.parse_args()

    monitor = DemoMonitor()
    monitor.start()
    monitor.record_health(trading_ready=True)
    status = monitor.get_status()
    config_file = Path(args.config)
    config_name = config_file.name if config_file.exists() else args.config
    print(
        f"[DemoMonitor] Started monitor with config={config_name}. "
        f"Health ready: {status.health_ready}, uptime: {status.uptime_seconds:.1f}s"
    )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
