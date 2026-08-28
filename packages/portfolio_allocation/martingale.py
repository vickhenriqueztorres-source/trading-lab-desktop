from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

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
    def recovery_stake(
        config: BoundedMartingaleConfig,
        state: BoundedMartingaleState,
        *,
        outstanding_loss_minor_units: int,
        net_profit_ratio: Decimal,
        remaining_loss_budget_minor_units: int,
    ) -> Money:
        """Return a quote-aware stake that recovers the outstanding sequence loss.

        A full recovery is preferred.  When it does not fit, the outstanding loss is
        divided over the remaining configured recovery attempts.  No result is ever
        silently clamped: an unaffordable recovery is rejected before reservation.
        """

        if state.step <= 0 or outstanding_loss_minor_units <= 0:
            return config.base_stake
        if state.step > config.max_steps:
            raise ValueError("martingale state exceeds configured max steps")
        if type(outstanding_loss_minor_units) is not int:
            raise TypeError("outstanding martingale loss must use integer minor units")
        if type(remaining_loss_budget_minor_units) is not int:
            raise TypeError("remaining martingale budget must use integer minor units")
        if (
            not isinstance(net_profit_ratio, Decimal)
            or not net_profit_ratio.is_finite()
            or net_profit_ratio <= 0
        ):
            raise ValueError("martingale quote net profit ratio must be positive")

        hard_cap = min(
            config.max_stake.minor_units,
            remaining_loss_budget_minor_units,
        )
        if hard_cap <= 0:
            raise ValueError("martingale recovery has no remaining loss budget")

        def required_stake(target_profit_minor_units: int) -> int:
            return int(
                (Decimal(target_profit_minor_units) / net_profit_ratio).quantize(
                    Decimal("1"),
                    rounding=ROUND_CEILING,
                )
            )

        # A recovery entry must never become smaller than the configured base
        # stake.  Besides preserving the bounded progression semantics, the
        # base stake is already validated against the broker minimum.  Without
        # this floor, a tiny residual loss (for example USD 0.03 at a 9% net
        # return) produced an invalid USD 0.34 order and caused a rejection loop.
        full_recovery = max(
            config.base_stake.minor_units,
            required_stake(outstanding_loss_minor_units),
        )
        if full_recovery <= hard_cap:
            return Money(full_recovery, config.base_stake.currency)

        remaining_attempts = config.max_steps - state.step + 1
        target_slice = int(
            (Decimal(outstanding_loss_minor_units) / Decimal(remaining_attempts)).quantize(
                Decimal("1"),
                rounding=ROUND_CEILING,
            )
        )
        divided_recovery = max(
            config.base_stake.minor_units,
            required_stake(target_slice),
        )
        if divided_recovery <= hard_cap:
            return Money(divided_recovery, config.base_stake.currency)
        raise ValueError("martingale recovery exceeds configured safety limits")

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
