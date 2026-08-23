from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from packages.domain.models import Money
from packages.signal_arbitration.models import ArbitratedSignal


class AllocationReason(StrEnum):
    APPROVED = "APPROVED"
    BUDGET_EXCEEDED = "PORTFOLIO_BUDGET_EXCEEDED"
    STRATEGY_BUDGET_MISSING = "STRATEGY_BUDGET_MISSING"
    CURRENCY_MISMATCH = "PORTFOLIO_CURRENCY_MISMATCH"


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    requested: Money
    strategy_remaining: tuple[tuple[str, Money], ...]
    account_remaining: Money
    global_remaining: Money

    def __post_init__(self) -> None:
        names = tuple(strategy_id for strategy_id, _ in self.strategy_remaining)
        if not names or any(not name.strip() for name in names) or len(set(names)) != len(names):
            raise ValueError("strategy_remaining must have unique non-empty strategy ids")
        if self.requested.minor_units <= 0:
            raise ValueError("requested allocation must be positive")
        if any(amount.minor_units < 0 for _, amount in self.strategy_remaining):
            raise ValueError("strategy remaining budgets cannot be negative")
        if self.account_remaining.minor_units < 0 or self.global_remaining.minor_units < 0:
            raise ValueError("account/global remaining budgets cannot be negative")


@dataclass(frozen=True, slots=True)
class PortfolioAllocation:
    arbitrated_signal: ArbitratedSignal
    amount: Money
    limiting_scope: str | None


@dataclass(frozen=True, slots=True)
class AllocationDecision:
    reason: AllocationReason
    allocation: PortfolioAllocation | None

    def __post_init__(self) -> None:
        if (self.reason is AllocationReason.APPROVED) != (self.allocation is not None):
            raise ValueError("allocation decision reason/allocation mismatch")
