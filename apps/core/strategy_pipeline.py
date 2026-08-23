from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from apps.core.coordinator import PersistedOrder
from packages.domain.market import MarketCandle
from packages.domain.models import OrderRequest
from packages.portfolio_allocation import AllocationDecision, BudgetSnapshot, PortfolioAllocator
from packages.signal_arbitration import ArbitrationDecision, SignalArbiter
from packages.strategies import RuntimeContext, StrategyEvaluation, StrategyRuntimeManager
from packages.strategies.models import ArbitrationKey


class PipelineStage(StrEnum):
    STRATEGY_RUNTIME = "STRATEGY_RUNTIME"
    SIGNAL_ARBITER = "SIGNAL_ARBITER"
    PORTFOLIO_ALLOCATOR = "PORTFOLIO_ALLOCATOR"


@dataclass(frozen=True, slots=True)
class StrategyBatchItem:
    context: RuntimeContext
    candle: MarketCandle


@dataclass(frozen=True, slots=True)
class EntryPlan:
    arbitration_key: ArbitrationKey
    budget: BudgetSnapshot
    deadline_at: datetime
    dispatch: bool = True


@dataclass(frozen=True, slots=True)
class StrategyPipelineResult:
    evaluations: tuple[StrategyEvaluation, ...]
    arbitrations: tuple[ArbitrationDecision, ...]
    allocations: tuple[AllocationDecision, ...]
    persisted_orders: tuple[PersistedOrder, ...]


class OrderIntentPort(Protocol):
    def submit(self, request: OrderRequest, *, dispatch: bool = True) -> PersistedOrder: ...


class StrategyEntryPipeline:
    """The only Phase 0 composition from strategy output to the financial Core path."""

    def __init__(
        self,
        runtimes: StrategyRuntimeManager,
        arbiter: SignalArbiter,
        allocator: PortfolioAllocator,
        coordinator: OrderIntentPort,
        *,
        stage_observer: Callable[[PipelineStage], None] | None = None,
    ) -> None:
        self._runtimes = runtimes
        self._arbiter = arbiter
        self._allocator = allocator
        self._coordinator = coordinator
        self._stage_observer = stage_observer or (lambda _stage: None)

    def process_batch(
        self,
        items: tuple[StrategyBatchItem, ...],
        plans: tuple[EntryPlan, ...],
        *,
        entitled_packs: frozenset[str],
        now: datetime,
    ) -> StrategyPipelineResult:
        plan_by_key = {plan.arbitration_key: plan for plan in plans}
        if len(plan_by_key) != len(plans):
            raise ValueError("entry plans must have unique arbitration keys")
        evaluations = tuple(
            self._runtimes.evaluate(
                item.context,
                item.candle,
                entitled_packs=entitled_packs,
            )
            for item in items
        )
        self._stage_observer(PipelineStage.STRATEGY_RUNTIME)
        signals = tuple(
            evaluation.signal for evaluation in evaluations if evaluation.signal is not None
        )
        arbitrations = self._arbiter.arbitrate_all(signals, now=now)
        self._stage_observer(PipelineStage.SIGNAL_ARBITER)
        allocations: list[AllocationDecision] = []
        persisted: list[PersistedOrder] = []
        for decision in arbitrations:
            signal = decision.arbitrated_signal
            if signal is None:
                continue
            plan = plan_by_key.get(decision.arbitration_key)
            if plan is None:
                raise ValueError("missing entry plan for arbitrated context")
            allocation = self._allocator.allocate(signal, plan.budget)
            allocations.append(allocation)
            self._stage_observer(PipelineStage.PORTFOLIO_ALLOCATOR)
            if allocation.allocation is None:
                continue
            approved = allocation.allocation
            primary = approved.arbitrated_signal.primary_context
            request = OrderRequest(
                correlation_id=approved.arbitrated_signal.correlation_id,
                broker=primary.broker,
                account_id=primary.account_id,
                product=primary.product,
                symbol=primary.symbol,
                direction=approved.arbitrated_signal.direction,
                amount=approved.amount,
                strategy_id=primary.strategy_id,
                strategy_version=primary.strategy_version,
                deadline_at=plan.deadline_at,
            )
            persisted.append(self._coordinator.submit(request, dispatch=plan.dispatch))
        return StrategyPipelineResult(
            evaluations=evaluations,
            arbitrations=arbitrations,
            allocations=tuple(allocations),
            persisted_orders=tuple(persisted),
        )
