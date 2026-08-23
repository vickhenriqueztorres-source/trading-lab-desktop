from __future__ import annotations

from packages.portfolio_allocation.models import (
    AllocationDecision,
    AllocationReason,
    BudgetSnapshot,
    PortfolioAllocation,
)
from packages.signal_arbitration.models import ArbitratedSignal


class PortfolioAllocator:
    """Pure budget ceiling calculation; the Risk Ledger remains the financial authority."""

    def allocate(
        self,
        signal: ArbitratedSignal,
        budget: BudgetSnapshot,
    ) -> AllocationDecision:
        currencies = {
            budget.requested.currency,
            budget.account_remaining.currency,
            budget.global_remaining.currency,
            *(amount.currency for _, amount in budget.strategy_remaining),
        }
        if len(currencies) != 1:
            return AllocationDecision(AllocationReason.CURRENCY_MISMATCH, None)
        strategy_budgets = dict(budget.strategy_remaining)
        source_ids = {strategy_id for strategy_id, _ in signal.source_strategy_keys}
        if not source_ids.issubset(strategy_budgets):
            return AllocationDecision(AllocationReason.STRATEGY_BUDGET_MISSING, None)
        ceilings = [
            ("ACCOUNT", budget.account_remaining.minor_units),
            ("GLOBAL", budget.global_remaining.minor_units),
            *(
                (f"STRATEGY:{strategy_id}", strategy_budgets[strategy_id].minor_units)
                for strategy_id in sorted(source_ids)
            ),
        ]
        limiting_scope, ceiling = min(ceilings, key=lambda item: (item[1], item[0]))
        if budget.requested.minor_units > ceiling:
            return AllocationDecision(AllocationReason.BUDGET_EXCEEDED, None)
        return AllocationDecision(
            AllocationReason.APPROVED,
            PortfolioAllocation(
                arbitrated_signal=signal,
                amount=budget.requested,
                limiting_scope=limiting_scope if ceiling == budget.requested.minor_units else None,
            ),
        )
