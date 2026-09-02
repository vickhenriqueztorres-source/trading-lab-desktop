from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from apps.core import EntryPlan, StrategyBatchItem, StrategyEntryPipeline
from apps.core.coordinator import OrderCoordinator
from apps.core.health import HealthGate
from apps.core.iqoption_auto_trader import IqOptionAutoTrader
from apps.core.iqoption_risk_config import IqOptionRiskConfig
from apps.core.runtime import CoreRuntime
from apps.simulated_worker.worker import SimulatedWorker
from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Money, WorkerOutcome
from packages.persistence import SingleDatabaseWriter, StateReader
from packages.portfolio_allocation import BudgetSnapshot, PortfolioAllocator
from packages.signal_arbitration import SignalArbiter
from packages.strategies import RuntimeContext, StrategyRuntimeManager
from packages.strategies.iqoption_rsi import (
    IQOPTION_RSI_ARTIFACT,
    IQOPTION_RSI_STRATEGY_ID,
    IQOptionRsiDemoStrategy,
    iqoption_rsi_manifest,
)
from packages.strategy_catalog import (
    StrategyCatalog,
    ValidationEvidence,
    ValidationRegistry,
    ValidationStage,
)


def _released_catalog() -> StrategyCatalog:
    registry = ValidationRegistry()
    manifest = iqoption_rsi_manifest()
    started = datetime(2026, 8, 1, tzinfo=UTC)
    for stage in ValidationStage:
        registry.record(
            ValidationEvidence(
                evidence_id=f"e2e-rsi-{stage.value.lower()}",
                strategy_id=manifest.strategy_id,
                strategy_version=manifest.version,
                report_id=manifest.validation_report_id,
                stage=stage,
                approved=True,
                broker=Broker.IQ_OPTION,
                product="BINARY_OPTION",
                symbol="EURUSD-OTC",
                timeframe_seconds=60,
                dataset_id=f"e2e-{stage.value.lower()}",
                period_start=started,
                period_end=started + timedelta(days=1),
                metrics=(("sample_count", Decimal("15")),),
            )
        )
    catalog = StrategyCatalog(registry)
    catalog.register(manifest, IQOptionRsiDemoStrategy(), IQOPTION_RSI_ARTIFACT)
    return catalog


def _context() -> RuntimeContext:
    return RuntimeContext(
        strategy_id=IQOPTION_RSI_STRATEGY_ID,
        strategy_version="1.0.0",
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACCOUNT",
        product="BINARY_OPTION",
        symbol="EURUSD-OTC",
        timeframe_seconds=60,
        configuration_version="rsi-demo-v1",
    )


def _candle(index: int) -> MarketCandle:
    opened = datetime(2026, 8, 31, 12, index, tzinfo=UTC)
    close = Decimal(100 - index)
    return MarketCandle(
        broker=Broker.IQ_OPTION,
        broker_symbol="EURUSD-OTC",
        timeframe_seconds=60,
        open_time=opened,
        close_time=opened + timedelta(minutes=1),
        open=close,
        high=close + Decimal("0.0001"),
        low=close - Decimal("0.0001"),
        close=close,
        is_closed=True,
    )


def test_rsi_practice_signal_reaches_persist_before_dispatch_core_path(tmp_path: Path) -> None:
    catalog = _released_catalog()
    writer = SingleDatabaseWriter(tmp_path / "rsi-practice.db")
    worker = SimulatedWorker([WorkerOutcome.ACCEPTED])
    pipeline = StrategyEntryPipeline(
        StrategyRuntimeManager(catalog),
        SignalArbiter(catalog),
        PortfolioAllocator(),
        OrderCoordinator(writer, worker, HealthGate()),
    )
    context = _context()
    plan = EntryPlan(
        arbitration_key=context.arbitration_key,
        budget=BudgetSnapshot(
            requested=Money(100, "USD"),
            strategy_remaining=((IQOPTION_RSI_STRATEGY_ID, Money(100, "USD")),),
            account_remaining=Money(100, "USD"),
            global_remaining=Money(100, "USD"),
        ),
        deadline_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    try:
        last = None
        for index in range(15):
            candle = _candle(index)
            last = pipeline.process_batch(
                (StrategyBatchItem(context, candle),),
                (plan,),
                entitled_packs=frozenset({"iqoption-practice-candidates"}),
                now=candle.close_time,
            )

        assert last is not None
        assert len(last.persisted_orders) == 1
        assert len(worker.received) == 1
        command = worker.received[0]
        assert command.broker is Broker.IQ_OPTION
        assert command.account_id == "PRACTICE_ACCOUNT"
        assert command.symbol == "EURUSD-OTC"
        assert command.amount == Money(100, "USD")
        reader = StateReader(writer.path)
        assert reader.count("trade_intents") == 1
        assert reader.count("risk_reservations") == 1
        assert reader.count("outbox_messages") == 1
        assert reader.count("orders") == 1
    finally:
        writer.close()


def test_iqoption_auto_trader_persists_before_worker_submission(tmp_path: Path) -> None:
    class EventCapableWorker(SimulatedWorker):
        @staticmethod
        def receive_order_event(timeout: float = 0.0) -> None:
            del timeout
            return None

    candles = [_candle(index) for index in range(20)]

    class MarketClient:
        @staticmethod
        def market_history(
            _symbol: str,
            *,
            style: str,
            count: int,
            timeframe_seconds: int,
        ) -> tuple[list[object], list[MarketCandle]]:
            assert (style, count, timeframe_seconds) == ("candles", 20, 60)
            return [], candles

    runtime = CoreRuntime(tmp_path / "auto-core")
    worker = EventCapableWorker([WorkerOutcome.ACCEPTED])
    runtime.start()
    try:
        runtime.attach_iqoption_worker(worker)
        assert runtime.resume_new_entries_for(Broker.IQ_OPTION, "IQOPTION_PRACTICE")
        trader = IqOptionAutoTrader(
            supervisor_provider=lambda: SimpleNamespace(client=MarketClient()),
            runtime_provider=lambda: runtime,
            risk_config_provider=lambda: IqOptionRiskConfig(symbol="EURUSD-OTC"),
            operator_armed=lambda: True,
        )

        trader._evaluate_cycle()

        assert len(worker.received) == 1
        assert worker.received[0].broker is Broker.IQ_OPTION
        assert runtime.reader.count("trade_intents") == 1
        assert runtime.reader.count("risk_reservations") == 1
        assert runtime.reader.count("outbox_messages") == 1
        assert runtime.reader.count("orders") == 1
        persisted = runtime.reader.list_nonterminal_orders()
        assert len(persisted) == 1
        assert persisted[0]["state"] == "ACCEPTED"
        assert trader.status_reason.startswith("ORDEM_ACEITA:")
    finally:
        runtime.shutdown()
