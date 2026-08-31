"""Small, dependency-free SLO evaluator used by the control plane.

The monitor deliberately consumes already-redacted measurements.  It never reads
financial state and it does not make trading decisions; a caller may use a
critical status as an operational alert and the existing Health Gate remains the
authority for blocking entries.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock


class SLOSeverity(StrEnum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class SLOConfig:
    name: str
    target: float
    window_seconds: float = 30 * 24 * 60 * 60
    warning_burn_rate: float = 2.0
    critical_burn_rate: float = 10.0
    threshold: float | None = None

    def __post_init__(self) -> None:
        if not self.name or not 0.0 <= self.target <= 1.0:
            raise ValueError("SLO name and target are invalid")
        if self.window_seconds <= 0:
            raise ValueError("SLO window must be positive")


@dataclass(frozen=True, slots=True)
class SLOStatus:
    name: str
    target: float
    total_events: int
    bad_events: int
    error_rate: float
    burn_rate: float
    remaining_budget: float
    projected_breach: bool
    severity: SLOSeverity

    @property
    def budget_remaining(self) -> float:
        """Compatibility spelling used by dashboard adapters."""
        return self.remaining_budget


@dataclass(frozen=True, slots=True)
class _Sample:
    timestamp: float
    good: bool


DEFAULT_SLOS: tuple[SLOConfig, ...] = (
    SLOConfig("availability_of_trading_ready", 0.995),
    SLOConfig("order_gateway_ack_p95_ms", 0.95, threshold=500.0),
    SLOConfig("order_gateway_ack_p99_ms", 0.99, threshold=1000.0),
    SLOConfig("reconciliation_p95_seconds", 0.95, threshold=30.0),
    SLOConfig("reconciliation_p99_seconds", 0.99, threshold=60.0),
    SLOConfig("duplicate_internal_submission_rate", 1.0),
    SLOConfig("unknown_orders_unresolved_over_15m", 1.0),
)


class SLOMonitor:
    """Calculate burn rate and remaining error budget over bounded windows."""

    def __init__(
        self,
        configs: Iterable[SLOConfig] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        selected = tuple(configs or DEFAULT_SLOS)
        if not selected or len({item.name for item in selected}) != len(selected):
            raise ValueError("SLO names must be unique")
        self._configs = {item.name: item for item in selected}
        self._samples: dict[str, deque[_Sample]] = defaultdict(deque)
        self._lock = Lock()
        self._clock = clock

    @property
    def configs(self) -> tuple[SLOConfig, ...]:
        return tuple(self._configs.values())

    def record(self, name: str, *, good: bool, timestamp: float | None = None) -> None:
        if name not in self._configs:
            raise KeyError(name)
        now = self._clock() if timestamp is None else timestamp
        with self._lock:
            samples = self._samples[name]
            samples.append(_Sample(now, bool(good)))
            self._prune(name, now)

    def observe_latency(self, name: str, latency: float, *, unit: str = "seconds") -> None:
        config = self._configs[name]
        if config.threshold is None:
            raise ValueError(f"{name} is not a latency SLO")
        value = latency * 1000.0 if unit == "seconds" and "ms" in name else latency
        self.record(name, good=value <= config.threshold)

    def _prune(self, name: str, now: float) -> None:
        cutoff = now - self._configs[name].window_seconds
        samples = self._samples[name]
        while samples and samples[0].timestamp < cutoff:
            samples.popleft()

    def calculate_burn_rate(self, name: str) -> float:
        status = self.status(name)
        return status.burn_rate

    def remaining_budget(self, name: str) -> float:
        return self.status(name).remaining_budget

    def project_breach(self, name: str) -> bool:
        return self.status(name).projected_breach

    def status(self, name: str) -> SLOStatus:
        config = self._configs[name]
        with self._lock:
            now = self._clock()
            self._prune(name, now)
            samples = tuple(self._samples[name])
        total = len(samples)
        bad = sum(1 for sample in samples if not sample.good)
        error_rate = round(bad / total, 12) if total else 0.0
        allowed = 1.0 - config.target
        burn = (
            round(error_rate / allowed, 12) if allowed > 0 else (0.0 if bad == 0 else float("inf"))
        )
        remaining = round(max(0.0, 1.0 - burn), 12) if allowed > 0 else (1.0 if bad == 0 else 0.0)
        projected = total > 0 and burn >= 1.0
        if burn >= config.critical_burn_rate or (allowed == 0 and bad > 0):
            severity = SLOSeverity.CRITICAL
        elif burn >= config.warning_burn_rate:
            severity = SLOSeverity.WARNING
        else:
            severity = SLOSeverity.HEALTHY
        return SLOStatus(
            name, config.target, total, bad, error_rate, burn, remaining, projected, severity
        )

    def get_status(self, name: str | None = None) -> SLOStatus | dict[str, SLOStatus]:
        if name is not None:
            return self.status(name)
        return {item.name: self.status(item.name) for item in self._configs.values()}


__all__ = ["DEFAULT_SLOS", "SLOConfig", "SLOMonitor", "SLOSeverity", "SLOStatus"]
