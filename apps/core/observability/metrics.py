"""Minimal in-process counters and latency histogram."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock


class Metrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self.orders_total = 0
        self.order_unknown_total = 0
        self.order_reconciliation_total = 0
        self.api_errors_total = 0
        self.orders_by_state: dict[str, int] = defaultdict(int)
        self.order_latency_seconds: list[float] = []

    def record_order(self, state: str, latency_seconds: float | None = None) -> None:
        with self._lock:
            self.orders_total += 1
            self.orders_by_state[str(state)] += 1
            if str(state).upper() == "UNKNOWN":
                self.order_unknown_total += 1
            if str(state).upper() == "RECONCILING":
                self.order_reconciliation_total += 1
            if latency_seconds is not None:
                self.order_latency_seconds.append(max(0.0, latency_seconds))

    def record_api_error(self) -> None:
        with self._lock:
            self.api_errors_total += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "orders_total": self.orders_total,
                "orders_by_state": dict(self.orders_by_state),
                "order_unknown_total": self.order_unknown_total,
                "order_reconciliation_total": self.order_reconciliation_total,
                "api_errors_total": self.api_errors_total,
                "order_latency_seconds": tuple(self.order_latency_seconds),
            }


__all__ = ["Metrics"]
