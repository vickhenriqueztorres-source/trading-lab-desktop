from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from packages.domain.models import Money


@dataclass(frozen=True, slots=True)
class BoundedMartingaleConfig:
    """Broker-neutral bounded stake progression owned by the Core allocator."""

    base_stake: Money
    multiplier: Decimal
    max_steps: int
    max_stake: Money
    daily_stop_loss: Money

    def __post_init__(self) -> None:
        if self.base_stake.currency != self.max_stake.currency:
            raise ValueError("martingale stake currencies must match")
        if self.base_stake.currency != self.daily_stop_loss.currency:
            raise ValueError("martingale stop-loss currency must match")
        if self.base_stake.minor_units <= 0:
            raise ValueError("martingale base stake must be positive")
        if self.max_stake.minor_units < self.base_stake.minor_units:
            raise ValueError("martingale max stake cannot be below base stake")
        if self.daily_stop_loss.minor_units <= 0:
            raise ValueError("martingale daily stop loss must be positive")
        if self.max_stake.minor_units > self.daily_stop_loss.minor_units:
            raise ValueError("martingale max stake cannot exceed daily stop loss")
        if (
            not isinstance(self.multiplier, Decimal)
            or not self.multiplier.is_finite()
            or not Decimal("1.10") <= self.multiplier <= Decimal("3.00")
        ):
            raise ValueError("martingale multiplier must be between 1.10 and 3.00")
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= 4:
            raise ValueError("martingale max steps must be between 1 and 4")


@dataclass(frozen=True, slots=True)
class BoundedMartingaleState:
    step: int = 0

    def __post_init__(self) -> None:
        if type(self.step) is not int or self.step < 0:
            raise ValueError("martingale step must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class BoundedMartingaleProjection:
    stakes: tuple[Money, ...]
    maximum_sequence_loss: Money


class BoundedMartingaleAllocator:
    """Pure progression math; every returned stake remains subject to the Risk Ledger."""

    @staticmethod
    def stake_for_step(
        config: BoundedMartingaleConfig,
        state: BoundedMartingaleState,
    ) -> Money:
        if state.step > config.max_steps:
            raise ValueError("martingale state exceeds configured max steps")
        calculated = (
            Decimal(config.base_stake.minor_units) * (config.multiplier**state.step)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        amount = min(int(calculated), config.max_stake.minor_units)
        return Money(amount, config.base_stake.currency)

    def project(self, config: BoundedMartingaleConfig) -> BoundedMartingaleProjection:
        stakes = tuple(
            self.stake_for_step(config, BoundedMartingaleState(step))
            for step in range(config.max_steps + 1)
        )
        return BoundedMartingaleProjection(
            stakes=stakes,
            maximum_sequence_loss=Money(
                sum(item.minor_units for item in stakes),
                config.base_stake.currency,
            ),
        )

    @staticmethod
    def after_settlement(
        config: BoundedMartingaleConfig,
        state: BoundedMartingaleState,
        realized_pnl_minor_units: int,
    ) -> BoundedMartingaleState:
        if type(realized_pnl_minor_units) is not int:
            raise TypeError("martingale settlement must use integer minor units")
        if realized_pnl_minor_units >= 0 or state.step >= config.max_steps:
            return BoundedMartingaleState()
        return BoundedMartingaleState(state.step + 1)
