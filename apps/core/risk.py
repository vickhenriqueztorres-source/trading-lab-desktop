from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from packages.domain.models import Money, OrderRequest
from packages.persistence.writer import RiskLimitExceededError

if TYPE_CHECKING:
    from apps.core.health import HealthGate


def canonicalize_symbol(symbol: str) -> str:
    """Normalizes symbol names across brokers (e.g. frxEURUSD -> EURUSD, OTC_EURUSD -> EURUSD)."""
    clean = symbol.strip().upper()
    if clean.startswith("FRX"):
        return clean[3:]
    if clean.startswith("OTC_"):
        return clean[4:]
    return clean


class RiskState(StrEnum):
    NORMAL = "NORMAL"
    COOLDOWN = "COOLDOWN"
    RISK_LOCKED = "RISK_LOCKED"


@dataclass(frozen=True, slots=True)
class GlobalRiskConfig:
    global_max_exposure_minor_units: int = 50000
    max_exposure_per_symbol_minor_units: int = 20000
    consolidated_daily_stop_loss_minor_units: int = 10000
    max_consecutive_losses: int = 3
    reference_currency: str = "USD"

    def __post_init__(self) -> None:
        if self.global_max_exposure_minor_units <= 0:
            raise ValueError("global_max_exposure_minor_units must be positive")
        if self.max_exposure_per_symbol_minor_units <= 0:
            raise ValueError("max_exposure_per_symbol_minor_units must be positive")
        if self.max_exposure_per_symbol_minor_units > self.global_max_exposure_minor_units:
            raise ValueError("max_exposure_per_symbol cannot exceed global_max_exposure")
        if self.consolidated_daily_stop_loss_minor_units <= 0:
            raise ValueError("consolidated_daily_stop_loss_minor_units must be positive")
        if self.max_consecutive_losses <= 0:
            raise ValueError("max_consecutive_losses must be positive")
        if len(self.reference_currency) != 3 or not self.reference_currency.isalpha():
            raise ValueError("reference_currency must be a 3-letter ISO code")


@dataclass(frozen=True, slots=True)
class RiskMetrics:
    global_exposure_minor_units: int
    global_max_exposure_minor_units: int
    consolidated_daily_pnl_minor_units: int
    consecutive_losses: int
    risk_state: RiskState


@dataclass(frozen=True, slots=True)
class RiskDecision:
    amount: Money


@dataclass(frozen=True, slots=True)
class RestoredExposure:
    reservation_id: str
    broker: str
    account_id: str
    amount: Money
    symbol: str = ""


class RiskLedger:
    """Consolidated Global Risk Ledger managing cross-broker limits, stop loss and cooldowns."""

    def __init__(self, config: GlobalRiskConfig | None = None) -> None:
        self._lock = threading.RLock()
        self._config = config or GlobalRiskConfig()
        self._active_reservations: dict[str, RestoredExposure] = {}
        self._daily_pnl_minor_units: int = 0
        self._consecutive_losses: int = 0
        self._risk_state: RiskState = RiskState.NORMAL

    @property
    def config(self) -> GlobalRiskConfig:
        with self._lock:
            return self._config

    @property
    def risk_state(self) -> RiskState:
        with self._lock:
            return self._risk_state

    @property
    def restored_exposure(self) -> tuple[RestoredExposure, ...]:
        with self._lock:
            return tuple(self._active_reservations.values())

    @property
    def active_exposure_minor_units(self) -> int:
        with self._lock:
            return sum(exp.amount.minor_units for exp in self._active_reservations.values())

    def active_symbol_exposure_minor_units(self, symbol: str) -> int:
        target = canonicalize_symbol(symbol)
        with self._lock:
            return sum(
                exp.amount.minor_units
                for exp in self._active_reservations.values()
                if canonicalize_symbol(exp.symbol) == target
            )

    def restore(self, reservations: Iterable[Mapping[str, object]]) -> None:
        with self._lock:
            self._active_reservations.clear()
            for row in reservations:
                reservation_id = row.get("reservation_id")
                broker = row.get("broker")
                account_id = row.get("account_id")
                amount_minor = row.get("amount_minor")
                currency = row.get("currency")
                symbol = str(row.get("symbol") or "")
                if (
                    not isinstance(reservation_id, str)
                    or not isinstance(broker, str)
                    or not isinstance(account_id, str)
                    or isinstance(amount_minor, bool)
                    or not isinstance(amount_minor, int)
                    or not isinstance(currency, str)
                ):
                    raise ValueError("invalid persisted risk reservation")
                self._active_reservations[reservation_id] = RestoredExposure(
                    reservation_id=reservation_id,
                    broker=broker,
                    account_id=account_id,
                    amount=Money(amount_minor, currency),
                    symbol=symbol,
                )

    def reserve(self, request: OrderRequest, health_gate: HealthGate | None = None) -> RiskDecision:
        with self._lock:
            if self._risk_state is RiskState.RISK_LOCKED:
                if health_gate is not None:
                    health_gate.block("HG_DAILY_STOP_REACHED")
                raise RiskLimitExceededError(
                    "HG_DAILY_STOP_REACHED",
                    "Consolidated daily stop loss reached: risk is locked.",
                )
            if self._risk_state is RiskState.COOLDOWN:
                if health_gate is not None:
                    health_gate.block("HG_COOLDOWN_ACTIVE")
                raise RiskLimitExceededError(
                    "HG_COOLDOWN_ACTIVE",
                    "Consecutive loss cooldown active: risk is locked.",
                )
            if request.amount.minor_units <= 0:
                raise ValueError("risk reservation must be positive")

            # 1. Check Canonical Symbol Limit cross-broker
            req_canonical = canonicalize_symbol(request.symbol)
            current_symbol = sum(
                exp.amount.minor_units
                for exp in self._active_reservations.values()
                if canonicalize_symbol(exp.symbol) == req_canonical
            )
            if (
                current_symbol + request.amount.minor_units
                > self._config.max_exposure_per_symbol_minor_units
            ):
                raise RiskLimitExceededError(
                    "HG_SYMBOL_EXPOSURE_LIMIT_EXCEEDED",
                    f"Active exposure on symbol {req_canonical} "
                    f"({current_symbol + request.amount.minor_units}) exceeds limit "
                    f"({self._config.max_exposure_per_symbol_minor_units}).",
                )

            # 2. Check Consolidated Global Limit cross-broker
            current_global = sum(
                exp.amount.minor_units for exp in self._active_reservations.values()
            )
            if (
                current_global + request.amount.minor_units
                > self._config.global_max_exposure_minor_units
            ):
                raise RiskLimitExceededError(
                    "HG_GLOBAL_EXPOSURE_EXCEEDED",
                    f"Active global exposure ({current_global + request.amount.minor_units}) "
                    f"exceeds limit ({self._config.global_max_exposure_minor_units}).",
                )

            return RiskDecision(amount=request.amount)

    def register_active_reservation(
        self,
        reservation_id: str,
        broker: str,
        account_id: str,
        symbol: str,
        amount: Money,
    ) -> None:
        with self._lock:
            self._active_reservations[reservation_id] = RestoredExposure(
                reservation_id=reservation_id,
                broker=broker,
                account_id=account_id,
                amount=amount,
                symbol=symbol,
            )

    def release_reservation(self, reservation_id: str) -> None:
        with self._lock:
            self._active_reservations.pop(reservation_id, None)

    def apply_realized_pnl(
        self,
        broker: str,
        account_id: str,
        pnl_minor_units: int,
        currency: str,
        health_gate: HealthGate | None = None,
    ) -> None:
        with self._lock:
            self._daily_pnl_minor_units += pnl_minor_units
            if pnl_minor_units < 0:
                self._consecutive_losses += 1
            elif pnl_minor_units > 0:
                self._consecutive_losses = 0

            # Daily Stop Loss check
            if (
                self._daily_pnl_minor_units
                <= -self._config.consolidated_daily_stop_loss_minor_units
            ):
                self._risk_state = RiskState.RISK_LOCKED
                if health_gate is not None:
                    health_gate.block("HG_DAILY_STOP_REACHED")
            elif self._consecutive_losses >= self._config.max_consecutive_losses:
                self._risk_state = RiskState.COOLDOWN
                if health_gate is not None:
                    health_gate.block("HG_COOLDOWN_ACTIVE")

    def reset_daily_pnl(self, health_gate: HealthGate | None = None) -> None:
        with self._lock:
            self._daily_pnl_minor_units = 0
            self._consecutive_losses = 0
            self._risk_state = RiskState.NORMAL
            if health_gate is not None:
                health_gate.clear("HG_DAILY_STOP_REACHED")
                health_gate.clear("HG_COOLDOWN_ACTIVE")

    def reset_cooldown(self, health_gate: HealthGate | None = None) -> None:
        with self._lock:
            self._consecutive_losses = 0
            if self._risk_state is RiskState.COOLDOWN:
                self._risk_state = RiskState.NORMAL
            if health_gate is not None:
                health_gate.clear("HG_COOLDOWN_ACTIVE")

    def get_metrics(self) -> RiskMetrics:
        with self._lock:
            global_exp = sum(exp.amount.minor_units for exp in self._active_reservations.values())
            return RiskMetrics(
                global_exposure_minor_units=global_exp,
                global_max_exposure_minor_units=self._config.global_max_exposure_minor_units,
                consolidated_daily_pnl_minor_units=self._daily_pnl_minor_units,
                consecutive_losses=self._consecutive_losses,
                risk_state=self._risk_state,
            )
