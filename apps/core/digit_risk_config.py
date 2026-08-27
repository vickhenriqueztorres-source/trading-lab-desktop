from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite

from packages.domain.models import Money
from packages.portfolio_allocation import (
    BoundedMartingaleAllocator,
    BoundedMartingaleConfig,
)

DERIV_SYNTHETIC_INDEX_ALLOWLIST = frozenset(
    {
        "R_10",
        "R_25",
        "R_50",
        "R_75",
        "R_100",
        "1HZ10V",
        "1HZ15V",
        "1HZ25V",
        "1HZ30V",
        "1HZ50V",
        "1HZ75V",
        "1HZ90V",
        "1HZ100V",
    }
)
DERIV_BOUNDED_STAKE_PRODUCTS = frozenset(
    {"DIGITDIFF", "DIGITOVER", "DIGITUNDER", "DIGITEVEN", "DIGITODD"}
)
DERIV_DIGIT_STRATEGY_ALLOWLIST = frozenset(
    {
        "tail-probability-edge",
        "selective-differs-edge",
        "parity-regime-edge",
    }
)


@dataclass(frozen=True, slots=True)
class DigitRiskConfig:
    """Immutable Core-owned limits shared by the three Deriv digit strategies."""

    stake_minor_units: int = 100
    daily_stop_loss_minor_units: int = 5000
    daily_take_profit_minor_units: int = 3000
    max_consecutive_losses: int = 1
    cooldown_seconds_after_loss: float = 30.0
    min_quantum_confidence_pct: Decimal = Decimal("92.5")
    selected_symbol: str = "R_100"
    currency: str = "USD"
    auto_select_symbol: bool = True
    active_strategy_id: str = "tail-probability-edge"
    martingale_enabled: bool = False
    martingale_multiplier: Decimal = Decimal("2.00")
    martingale_max_steps: int = 2
    martingale_max_stake_minor_units: int = 400


def bounded_martingale_config(config: DigitRiskConfig) -> BoundedMartingaleConfig:
    return BoundedMartingaleConfig(
        base_stake=Money(config.stake_minor_units, config.currency),
        multiplier=config.martingale_multiplier,
        max_steps=config.martingale_max_steps,
        max_stake=Money(config.martingale_max_stake_minor_units, config.currency),
        daily_stop_loss=Money(config.daily_stop_loss_minor_units, config.currency),
    )


def is_bounded_digit_product(product: str) -> bool:
    return product.strip().upper() in DERIV_BOUNDED_STAKE_PRODUCTS


def projected_martingale_stakes(config: DigitRiskConfig) -> tuple[int, ...]:
    if not config.martingale_enabled:
        return (config.stake_minor_units,)
    projection = BoundedMartingaleAllocator().project(bounded_martingale_config(config))
    return tuple(item.minor_units for item in projection.stakes)


def validate_digit_risk_config(config: DigitRiskConfig) -> tuple[bool, str | None]:
    if type(config.stake_minor_units) is not int or config.stake_minor_units < 35:
        return False, "DIGIT_RISK_STAKE_BELOW_MINIMUM"
    if (
        type(config.daily_stop_loss_minor_units) is not int
        or config.daily_stop_loss_minor_units <= 0
    ):
        return False, "DIGIT_RISK_STOP_LOSS_INVALID"
    if (
        type(config.daily_take_profit_minor_units) is not int
        or config.daily_take_profit_minor_units <= 0
    ):
        return False, "DIGIT_RISK_TAKE_PROFIT_INVALID"
    if (
        type(config.max_consecutive_losses) is not int
        or not 1 <= config.max_consecutive_losses <= 5
    ):
        return False, "DIGIT_RISK_CONSECUTIVE_LOSSES_INVALID"
    if (
        isinstance(config.cooldown_seconds_after_loss, bool)
        or not isinstance(config.cooldown_seconds_after_loss, int | float)
        or not isfinite(config.cooldown_seconds_after_loss)
        or config.cooldown_seconds_after_loss <= 0
    ):
        return False, "DIGIT_RISK_COOLDOWN_INVALID"
    if (
        not isinstance(config.min_quantum_confidence_pct, Decimal)
        or not config.min_quantum_confidence_pct.is_finite()
        or not Decimal("80.0") <= config.min_quantum_confidence_pct <= Decimal("99.0")
    ):
        return False, "DIGIT_RISK_CONFIDENCE_INVALID"
    if config.selected_symbol not in DERIV_SYNTHETIC_INDEX_ALLOWLIST:
        return False, "DIGIT_RISK_SYMBOL_NOT_ALLOWED"
    if config.currency != "USD":
        return False, "DIGIT_RISK_CURRENCY_NOT_SUPPORTED"
    if type(config.auto_select_symbol) is not bool:
        return False, "DIGIT_RISK_AUTO_SYMBOL_INVALID"
    if config.active_strategy_id not in DERIV_DIGIT_STRATEGY_ALLOWLIST:
        return False, "DIGIT_RISK_STRATEGY_NOT_ALLOWED"
    if type(config.martingale_enabled) is not bool:
        return False, "DIGIT_MARTINGALE_ENABLED_INVALID"
    if (
        not isinstance(config.martingale_multiplier, Decimal)
        or not config.martingale_multiplier.is_finite()
        or not Decimal("1.10") <= config.martingale_multiplier <= Decimal("3.00")
    ):
        return False, "DIGIT_MARTINGALE_MULTIPLIER_INVALID"
    if type(config.martingale_max_steps) is not int or not 1 <= config.martingale_max_steps <= 4:
        return False, "DIGIT_MARTINGALE_STEPS_INVALID"
    if (
        type(config.martingale_max_stake_minor_units) is not int
        or config.martingale_max_stake_minor_units <= 0
        or config.martingale_max_stake_minor_units > config.daily_stop_loss_minor_units
    ):
        return False, "DIGIT_MARTINGALE_MAX_STAKE_INVALID"
    if config.martingale_enabled:
        if config.martingale_max_stake_minor_units < config.stake_minor_units:
            return False, "DIGIT_MARTINGALE_MAX_STAKE_INVALID"
        if config.max_consecutive_losses < config.martingale_max_steps + 1:
            return False, "DIGIT_MARTINGALE_LOSS_LIMIT_TOO_LOW"
        try:
            projected_loss = sum(projected_martingale_stakes(config))
        except ValueError:
            return False, "DIGIT_MARTINGALE_CONFIG_INVALID"
        if projected_loss > config.daily_stop_loss_minor_units:
            return False, "DIGIT_MARTINGALE_SEQUENCE_EXCEEDS_STOP_LOSS"
    return True, None
