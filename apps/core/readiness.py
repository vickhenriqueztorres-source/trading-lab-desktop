from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TradingReadinessSnapshot:
    """Single Core-owned explanation of process, recovery and trading readiness."""

    core_available: bool
    broker_process_ready: bool
    broker_authenticated: bool
    reconciliation_complete: bool
    risk_ready: bool
    clock_trusted: bool
    market_healthy: bool
    warmup_complete: bool
    safe_stop: bool
    armed: bool
    order_in_flight: bool
    ready_to_arm: bool
    ready_to_trade: bool
    blocking_reasons: tuple[str, ...]
