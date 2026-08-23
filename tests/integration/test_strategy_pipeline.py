from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from apps.core import EntryPlan, PipelineStage, StrategyBatchItem, StrategyEntryPipeline
from apps.core.coordinator import OrderCoordinator
from apps.core.health import HealthGate
from apps.core.risk import RiskDecision, RiskLedger
from apps.simulated_worker.worker import SimulatedWorker
from packages.domain.models import (
    BrokerOrderEvent,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderRequest,
    WorkerOutcome,
)
from packages.persistence import SingleDatabaseWriter, StateReader
from packages.portfolio_allocation import BudgetSnapshot, PortfolioAllocator
from packages.signal_arbitration import ArbitrationReason, SignalArbiter
from packages.strategies import StrategyRuntimeManager
from packages.strategy_catalog import (
    ReleaseStatus,
    StrategyCatalog,
    StrategyCatalogError,
    StrategyCatalogReason,
    ValidationRegistry,
)
from tests.helpers.strategy_fixtures import candle_for, context_for, register_released

ENTITLED = frozenset({"phase0-candidates"})


class TraceRiskLedger(RiskLedger):
    def __init__(self, trace: list[str]) -> None:
        super().__init__()
        self._trace = trace

    def reserve(self, request: OrderRequest, health_gate: HealthGate | None = None) -> RiskDecision:
        self._trace.append("RISK_LEDGER")
        return super().reserve(request, health_gate)


def plan_for(strategy_id: str, *, requested_minor: int = 1_000) -> EntryPlan:
    context = context_for(strategy_id)
    return EntryPlan(
        arbitration_key=context.arbitration_key,
        budget=BudgetSnapshot(
            requested=Money(requested_minor, "USD"),
            strategy_remaining=(
                ("pipeline-a", Money(1_000, "USD")),
                ("pipeline-b", Money(1_000, "USD")),
                ("pipeline-put", Money(1_000, "USD")),
            ),
            account_remaining=Money(1_000, "USD"),
            global_remaining=Money(1_000, "USD"),
        ),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )


def build_pipeline(
    path: Path,
    catalog: StrategyCatalog,
    trace: list[str] | None = None,
) -> tuple[StrategyEntryPipeline, SingleDatabaseWriter, SimulatedWorker]:
    writer = SingleDatabaseWriter(path)
    worker = SimulatedWorker([WorkerOutcome.ACCEPTED])
    stage_trace = trace if trace is not None else []
    coordinator = OrderCoordinator(
        writer,
        worker,
        HealthGate(),
        risk_ledger=TraceRiskLedger(stage_trace),
    )
    pipeline = StrategyEntryPipeline(
        StrategyRuntimeManager(catalog),
        SignalArbiter(catalog),
        PortfolioAllocator(),
        coordinator,
        stage_observer=lambda stage: stage_trace.append(stage.value),
    )
    return pipeline, writer, worker


def settlement_event(
    request: OrderRequest,
    order_id: str,
    broker_order_id: str,
) -> BrokerOrderEvent:
    now = datetime.now(UTC)
    canonical: dict[str, object] = {
        "event_id": str(uuid4()),
        "event_version": 1,
        "broker": request.broker.value,
        "account_id": request.account_id,
        "client_order_ref": order_id,
        "broker_order_id": broker_order_id,
        "correlation_id": request.correlation_id,
        "external_sequence": 1,
        "external_status": ExternalOrderStatus.SETTLED.value,
        "occurred_at": now.isoformat(),
        "observed_at": now.isoformat(),
        "product": request.product,
        "symbol": request.symbol,
        "direction": request.direction.value,
        "amount_minor": request.amount.minor_units,
        "currency": request.amount.currency,
        "result_minor": 100,
        "result_currency": request.amount.currency,
    }
    return BrokerOrderEvent.from_payload(
        {**canonical, "evidence_hash": BrokerOrderEvent.evidence_hash_for_payload(canonical)}
    )


def test_pipeline_proves_runtime_arbiter_allocator_risk_and_single_persistence(
    tmp_path: Path,
) -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    register_released(catalog, registry, "pipeline-a", direction=Direction.CALL)
    register_released(catalog, registry, "pipeline-b", direction=Direction.CALL)
    trace: list[str] = []
    pipeline, writer, worker = build_pipeline(tmp_path / "pipeline.db", catalog, trace)
    close = datetime(2026, 8, 20, 12, 1, tzinfo=UTC)
    try:
        result = pipeline.process_batch(
            (
                StrategyBatchItem(context_for("pipeline-a"), candle_for(close)),
                StrategyBatchItem(context_for("pipeline-b"), candle_for(close)),
            ),
            (plan_for("pipeline-a"),),
            entitled_packs=ENTITLED,
            now=close,
        )
        assert trace == [
            PipelineStage.STRATEGY_RUNTIME.value,
            PipelineStage.SIGNAL_ARBITER.value,
            PipelineStage.PORTFOLIO_ALLOCATOR.value,
            "RISK_LEDGER",
        ]
        assert result.arbitrations[0].reason is ArbitrationReason.CONSENSUS_NO_STAKE_SUM
        assert len(result.persisted_orders) == 1
        assert len(worker.received) == 1
        assert worker.received[0].amount.minor_units == 1_000
        assert StateReader(writer.path).count("trade_intents") == 1
    finally:
        writer.close()


def test_opposite_signals_and_exceeded_budget_never_reach_risk_or_worker(
    tmp_path: Path,
) -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    register_released(catalog, registry, "pipeline-a", direction=Direction.CALL)
    register_released(catalog, registry, "pipeline-put", direction=Direction.PUT)
    trace: list[str] = []
    pipeline, writer, worker = build_pipeline(tmp_path / "blocked.db", catalog, trace)
    close = datetime(2026, 8, 20, 12, 1, tzinfo=UTC)
    try:
        opposite = pipeline.process_batch(
            (
                StrategyBatchItem(context_for("pipeline-a"), candle_for(close)),
                StrategyBatchItem(context_for("pipeline-put"), candle_for(close)),
            ),
            (plan_for("pipeline-a"),),
            entitled_packs=ENTITLED,
            now=close,
        )
        assert opposite.arbitrations[0].reason is ArbitrationReason.OPPOSING_SIGNALS_CANCELLED
        assert opposite.persisted_orders == ()
        assert "RISK_LEDGER" not in trace
        assert worker.received == []

        second_pipeline, second_writer, second_worker = build_pipeline(
            tmp_path / "budget.db", catalog
        )
        try:
            budget = plan_for("pipeline-a", requested_minor=1_001)
            blocked = second_pipeline.process_batch(
                (StrategyBatchItem(context_for("pipeline-a"), candle_for(close)),),
                (budget,),
                entitled_packs=ENTITLED,
                now=close,
            )
            assert blocked.allocations[0].allocation is None
            assert blocked.persisted_orders == ()
            assert second_worker.received == []
            assert StateReader(second_writer.path).count("trade_intents") == 0
        finally:
            second_writer.close()
    finally:
        writer.close()


def test_suspension_blocks_new_signal_but_existing_order_still_settles(
    tmp_path: Path,
) -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    manifest = register_released(catalog, registry, "pipeline-a", direction=Direction.CALL)
    pipeline, writer, worker = build_pipeline(tmp_path / "suspension.db", catalog)
    reader = StateReader(writer.path)
    first_close = datetime(2026, 8, 20, 12, 1, tzinfo=UTC)
    try:
        first = pipeline.process_batch(
            (StrategyBatchItem(context_for("pipeline-a"), candle_for(first_close)),),
            (plan_for("pipeline-a"),),
            entitled_packs=ENTITLED,
            now=first_close,
        )
        persisted = first.persisted_orders[0]
        catalog.transition(manifest.strategy_id, manifest.version, ReleaseStatus.SUSPENDED)
        with pytest.raises(StrategyCatalogError) as suspended:
            pipeline.process_batch(
                (
                    StrategyBatchItem(
                        context_for("pipeline-a"),
                        candle_for(first_close + timedelta(minutes=1)),
                    ),
                ),
                (plan_for("pipeline-a"),),
                entitled_packs=ENTITLED,
                now=first_close + timedelta(minutes=1),
            )
        assert suspended.value.reason is StrategyCatalogReason.SUSPENDED
        assert len(worker.received) == 1
        assert reader.count("trade_intents") == 1

        command = worker.received[0]
        result = writer.apply_normalized_broker_event(
            settlement_event(
                OrderRequest(
                    correlation_id=command.correlation_id,
                    broker=command.broker,
                    account_id=command.account_id,
                    product=command.product,
                    symbol=command.symbol,
                    direction=command.direction,
                    amount=command.amount,
                    strategy_id="pipeline-a",
                    strategy_version="1.0.0",
                    deadline_at=command.deadline_at,
                ),
                persisted.order_id,
                f"SIM-{persisted.message_id}",
            )
        )
        assert result.order_state.value == "SETTLED"
        assert reader.reservation_for_intent(persisted.intent_id)["state"] == "RELEASED"
    finally:
        writer.close()


def test_entitlement_missing_fails_before_runtime_or_financial_state(tmp_path: Path) -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    register_released(catalog, registry, "pipeline-a", direction=Direction.CALL)
    pipeline, writer, worker = build_pipeline(tmp_path / "entitlement.db", catalog)
    close = datetime(2026, 8, 20, 12, 1, tzinfo=UTC)
    try:
        with pytest.raises(StrategyCatalogError) as missing:
            pipeline.process_batch(
                (StrategyBatchItem(context_for("pipeline-a"), candle_for(close)),),
                (plan_for("pipeline-a"),),
                entitled_packs=frozenset(),
                now=close,
            )
        assert missing.value.reason is StrategyCatalogReason.ENTITLEMENT_MISSING
        assert worker.received == []
        assert StateReader(writer.path).count("trade_intents") == 0
    finally:
        writer.close()
