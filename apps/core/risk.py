from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from math import ceil
from typing import TYPE_CHECKING

from apps.core.digit_risk_config import (
    DigitRiskConfig,
    bounded_martingale_config,
    is_bounded_digit_product,
    projected_martingale_stakes,
    validate_digit_risk_config,
)
from packages.domain.models import Money, OrderRequest
from packages.persistence.writer import RiskLimitExceededError
from packages.portfolio_allocation import (
    BoundedMartingaleAllocator,
    BoundedMartingaleState,
)

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
class DigitRiskMetrics:
    active_config: DigitRiskConfig
    daily_pnl_minor_units: int
    consecutive_losses: int
    cooldown_remaining_seconds: int
    martingale_step: int
    next_stake_minor_units: int
    projected_sequence_loss_minor_units: int
    recovery_symbol: str | None = None
    cumulative_sequence_loss_minor_units: int = 0


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

    def __init__(
        self,
        config: GlobalRiskConfig | None = None,
        *,
        digit_config: DigitRiskConfig | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        digit_runtime_expirer: Callable[[datetime], Mapping[str, object] | None] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._config = config or GlobalRiskConfig()
        initial_digit_config = digit_config or DigitRiskConfig()
        valid, reason = validate_digit_risk_config(initial_digit_config)
        if not valid:
            raise ValueError(reason)
        self._digit_config = initial_digit_config
        self._monotonic_clock = monotonic_clock
        self._utc_clock = utc_clock
        self._digit_runtime_expirer = digit_runtime_expirer
        self._digit_daily_pnl_minor_units = 0
        self._digit_consecutive_losses = 0
        self._digit_cooldown_deadline: float | None = None
        self._martingale_allocator = BoundedMartingaleAllocator()
        self._digit_martingale_state = BoundedMartingaleState()
        self._digit_recovery_symbol: str | None = None
        self._digit_cumulative_sequence_loss_minor_units = 0
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

    def digit_runtime_policy(self) -> dict[str, object]:
        config = self.digit_config
        serialized = json.dumps(
            {key: str(value) for key, value in asdict(config).items()},
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "config_fingerprint": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "currency": config.currency,
            "martingale_enabled": config.martingale_enabled,
            "martingale_max_steps": config.martingale_max_steps,
            "max_consecutive_losses": config.max_consecutive_losses,
            "cooldown_seconds": str(config.cooldown_seconds_after_loss),
        }

    def restore_digit_runtime(self, row: Mapping[str, object] | None) -> None:
        """Restore durable progression/cooldown state after startup or settlement."""

        if row is None:
            return
        with self._lock:
            if (
                str(row.get("config_fingerprint"))
                != self.digit_runtime_policy()["config_fingerprint"]
            ):
                raise ValueError("digit runtime configuration does not match active policy")
            self._digit_daily_pnl_minor_units = self._persisted_int(row, "daily_pnl_minor")
            self._digit_consecutive_losses = self._persisted_int(row, "consecutive_losses")
            self._digit_martingale_state = BoundedMartingaleState(
                self._persisted_int(row, "martingale_step")
            )
            pinned = row.get("pinned_symbol")
            self._digit_recovery_symbol = str(pinned) if isinstance(pinned, str) else None
            self._digit_cumulative_sequence_loss_minor_units = self._persisted_int(
                row, "cumulative_sequence_loss_minor"
            )
            started_raw = row.get("cooldown_started_at")
            if isinstance(started_raw, str):
                started = datetime.fromisoformat(started_raw)
                duration = float(str(row.get("cooldown_seconds")))
                remaining = (
                    started + timedelta(seconds=duration) - self._utc_clock()
                ).total_seconds()
                self._digit_cooldown_deadline = self._monotonic_clock() + max(0.0, remaining)
            else:
                self._digit_cooldown_deadline = None

    @staticmethod
    def _persisted_int(row: Mapping[str, object], field: str) -> int:
        value = row.get(field)
        if type(value) is not int:
            raise ValueError(f"invalid persisted digit runtime field: {field}")
        return value

    def reserve(self, request: OrderRequest, health_gate: HealthGate | None = None) -> RiskDecision:
        with self._lock:
            if is_bounded_digit_product(request.product):
                allowed, reason = self.check_digit_entry(self._digit_config, health_gate)
                if not allowed:
                    raise RiskLimitExceededError(
                        reason or "DIGIT_RISK_ENTRY_BLOCKED",
                        "Specialized DIGITDIFF risk gate blocked the entry.",
                    )
                if self._digit_config.martingale_enabled:
                    expected = self._current_digit_stake_minor_units()
                    if request.amount.minor_units != expected:
                        raise RiskLimitExceededError(
                            "DIGIT_RISK_STAKE_MISMATCH",
                            "DIGIT stake does not match the Core-owned bounded progression.",
                        )
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

    @property
    def digit_config(self) -> DigitRiskConfig:
        with self._lock:
            return self._digit_config

    def update_digit_risk_config(
        self,
        config: DigitRiskConfig,
        health_gate: HealthGate | None = None,
    ) -> tuple[bool, str | None]:
        valid, reason = validate_digit_risk_config(config)
        if not valid:
            return False, reason
        with self._lock:
            if config == self._digit_config:
                self.check_digit_entry(config, health_gate)
                return True, None
            if self._digit_martingale_state.step > 0 and config.martingale_enabled:
                return False, "DIGIT_MARTINGALE_SEQUENCE_ACTIVE"
            self._digit_config = config
            self._digit_martingale_state = BoundedMartingaleState()
            self.check_digit_entry(config, health_gate)
            return True, None

    def digit_entry_stake(self, health_gate: HealthGate | None = None) -> Money:
        with self._lock:
            allowed, reason = self.check_digit_entry(self._digit_config, health_gate)
            if not allowed:
                raise RiskLimitExceededError(
                    reason or "DIGIT_RISK_ENTRY_BLOCKED",
                    "DIGIT risk gate blocked stake allocation.",
                )
            return Money(
                self._current_digit_stake_minor_units(),
                self._digit_config.currency,
            )

    def check_digit_entry(
        self,
        config: DigitRiskConfig,
        health_gate: HealthGate | None = None,
    ) -> tuple[bool, str | None]:
        valid, reason = validate_digit_risk_config(config)
        if not valid:
            return False, reason
        with self._lock:
            if self._risk_state is RiskState.RISK_LOCKED:
                if health_gate is not None:
                    health_gate.block("HG_DAILY_STOP_REACHED")
                return False, "HG_DAILY_STOP_REACHED"
            if self._digit_daily_pnl_minor_units <= -config.daily_stop_loss_minor_units:
                if health_gate is not None:
                    health_gate.block("HG_DAILY_STOP_REACHED")
                return False, "HG_DAILY_STOP_REACHED"
            if self._digit_daily_pnl_minor_units >= config.daily_take_profit_minor_units:
                if health_gate is not None:
                    health_gate.block("HG_DAILY_TAKE_PROFIT_REACHED")
                return False, "HG_DAILY_TAKE_PROFIT_REACHED"
            if self._digit_cooldown_deadline is not None:
                if self._monotonic_clock() < self._digit_cooldown_deadline:
                    if health_gate is not None:
                        health_gate.block("HG_COOLDOWN_ACTIVE")
                    return False, "HG_COOLDOWN_ACTIVE"
                self._digit_cooldown_deadline = None
                self._digit_consecutive_losses = 0
                self._digit_martingale_state = BoundedMartingaleState()
                self._digit_recovery_symbol = None
                self._digit_cumulative_sequence_loss_minor_units = 0
                if self._digit_runtime_expirer is not None:
                    refreshed = self._digit_runtime_expirer(self._utc_clock())
                    if refreshed is not None:
                        self.restore_digit_runtime(refreshed)
                if health_gate is not None and self._risk_state is not RiskState.COOLDOWN:
                    health_gate.clear("HG_COOLDOWN_ACTIVE")
            if config.martingale_enabled:
                state = (
                    self._digit_martingale_state
                    if config == self._digit_config
                    else BoundedMartingaleState()
                )
                next_stake = self._digit_stake_minor_units(config, state)
                remaining_loss_budget = (
                    config.daily_stop_loss_minor_units + self._digit_daily_pnl_minor_units
                )
                if next_stake > remaining_loss_budget:
                    self._risk_state = RiskState.RISK_LOCKED
                    self._digit_martingale_state = BoundedMartingaleState()
                    if health_gate is not None:
                        health_gate.block("HG_DAILY_STOP_REACHED")
                    return False, "HG_DAILY_STOP_REACHED"
            return True, None

    def refresh_digit_health_gate(self, health_gate: HealthGate) -> None:
        """Refresh time-dependent digit blockers before the global entry-gate check."""

        self.check_digit_entry(self.digit_config, health_gate)

    def apply_digit_realized_pnl(
        self,
        pnl_minor_units: int,
        currency: str,
        health_gate: HealthGate | None = None,
    ) -> None:
        if type(pnl_minor_units) is not int:
            raise TypeError("digit P&L must use integer minor units")
        with self._lock:
            if currency != self._digit_config.currency:
                raise ValueError("digit P&L currency does not match active configuration")
            self._digit_daily_pnl_minor_units += pnl_minor_units
            if self._digit_config.martingale_enabled:
                self._digit_martingale_state = self._martingale_allocator.after_settlement(
                    bounded_martingale_config(self._digit_config),
                    self._digit_martingale_state,
                    pnl_minor_units,
                )
            else:
                self._digit_martingale_state = BoundedMartingaleState()
            if pnl_minor_units < 0:
                self._digit_cumulative_sequence_loss_minor_units += -pnl_minor_units
                self._digit_consecutive_losses += 1
                if self._digit_consecutive_losses >= self._digit_config.max_consecutive_losses:
                    self._digit_cooldown_deadline = (
                        self._monotonic_clock() + self._digit_config.cooldown_seconds_after_loss
                    )
                    if health_gate is not None:
                        health_gate.block("HG_COOLDOWN_ACTIVE")
            elif pnl_minor_units > 0:
                self._digit_consecutive_losses = 0
                self._digit_recovery_symbol = None
                self._digit_cumulative_sequence_loss_minor_units = 0

            self.check_digit_entry(self._digit_config, health_gate)

    def get_digit_metrics(self) -> DigitRiskMetrics:
        with self._lock:
            remaining = 0
            if self._digit_cooldown_deadline is not None:
                remaining = max(
                    0,
                    ceil(self._digit_cooldown_deadline - self._monotonic_clock()),
                )
            return DigitRiskMetrics(
                active_config=self._digit_config,
                daily_pnl_minor_units=self._digit_daily_pnl_minor_units,
                consecutive_losses=self._digit_consecutive_losses,
                cooldown_remaining_seconds=remaining,
                martingale_step=self._digit_martingale_state.step,
                next_stake_minor_units=self._current_digit_stake_minor_units(),
                projected_sequence_loss_minor_units=sum(
                    projected_martingale_stakes(self._digit_config)
                ),
                recovery_symbol=self._digit_recovery_symbol,
                cumulative_sequence_loss_minor_units=(
                    self._digit_cumulative_sequence_loss_minor_units
                ),
            )

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
            self._digit_daily_pnl_minor_units = 0
            self._digit_consecutive_losses = 0
            self._digit_cooldown_deadline = None
            self._digit_martingale_state = BoundedMartingaleState()
            self._digit_recovery_symbol = None
            self._digit_cumulative_sequence_loss_minor_units = 0
            if health_gate is not None:
                health_gate.clear("HG_DAILY_STOP_REACHED")
                health_gate.clear("HG_DAILY_TAKE_PROFIT_REACHED")
                health_gate.clear("HG_COOLDOWN_ACTIVE")

    def reset_cooldown(self, health_gate: HealthGate | None = None) -> None:
        with self._lock:
            self._consecutive_losses = 0
            self._digit_consecutive_losses = 0
            self._digit_cooldown_deadline = None
            self._digit_martingale_state = BoundedMartingaleState()
            self._digit_recovery_symbol = None
            self._digit_cumulative_sequence_loss_minor_units = 0
            if self._risk_state is RiskState.COOLDOWN:
                self._risk_state = RiskState.NORMAL
            if health_gate is not None:
                health_gate.clear("HG_COOLDOWN_ACTIVE")

    def _current_digit_stake_minor_units(self) -> int:
        return self._digit_stake_minor_units(
            self._digit_config,
            self._digit_martingale_state,
        )

    def _digit_stake_minor_units(
        self,
        config: DigitRiskConfig,
        state: BoundedMartingaleState,
    ) -> int:
        if not config.martingale_enabled:
            return config.stake_minor_units
        return self._martingale_allocator.stake_for_step(
            bounded_martingale_config(config),
            state,
        ).minor_units

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
