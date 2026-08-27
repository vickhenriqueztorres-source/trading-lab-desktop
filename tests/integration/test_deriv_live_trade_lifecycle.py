from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from apps.core.broker_events import BrokerEventProcessor
from apps.core.coordinator import OrderCoordinator
from apps.core.deriv_auto_trader import DerivDigitAutoTrader
from apps.core.deriv_telemetry import DerivTelemetrySnapshot, DerivTelemetrySource
from apps.core.health import HealthGate
from apps.core.risk import RiskLedger
from apps.deriv_worker.fake_transport import FakeDerivScenario, FakeDerivTransport
from apps.deriv_worker.order_session import DerivLiveOrderSession
from apps.deriv_worker.request_allowlist import DerivOperation
from packages.domain.market import MarketTick
from packages.domain.models import (
    Broker,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderRequest,
    OrderState,
    ReconciliationEvidence,
    ReconciliationSource,
)
from packages.market_data import DigitFrequencySnapshot
from packages.persistence.database import open_writer_connection
from packages.persistence.migrations import apply_migrations
from packages.persistence.reader import StateReader
from packages.persistence.writer import BrokerEventApplyStatus, SingleDatabaseWriter
from packages.strategies.deriv_digits import DerivDigitShadowEngine, DerivDigitStrategyId


def _services(
    db_path: Path,
) -> tuple[
    SingleDatabaseWriter,
    StateReader,
    RiskLedger,
    HealthGate,
]:
    connection = open_writer_connection(db_path)
    apply_migrations(connection)
    connection.close()
    return SingleDatabaseWriter(db_path), StateReader(db_path), RiskLedger(), HealthGate()


def _request() -> OrderRequest:
    return OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITAL_OPTION",
        symbol="frxEURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        strategy_id="live-demo-contract",
        strategy_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
    )


def _settle_queued_events(
    session: DerivLiveOrderSession,
    processor: BrokerEventProcessor,
) -> None:
    session.drain_contract_events(timeout=0)
    event = session.next_queued_event(timeout=0)
    while event is not None:
        processor.process(event)
        event = session.next_queued_event(timeout=0)


class _AutoTraderRuntime:
    dispatcher_started = True

    def __init__(
        self,
        coordinator: OrderCoordinator,
        reader: StateReader,
        risk_ledger: RiskLedger,
        health_gate: HealthGate,
    ) -> None:
        self._coordinator = coordinator
        self.reader = reader
        self.risk_ledger = risk_ledger
        self.health_gate = health_gate

    def submit(self, request: OrderRequest) -> object:
        return self._coordinator.submit(request)


def _digit_signal_snapshot(strategy_id: DerivDigitStrategyId) -> DerivTelemetrySnapshot:
    ticks = tuple(
        MarketTick(
            Broker.DERIV,
            "R_100",
            1_900_000_000 + index,
            Decimal(f"100.0{digit}"),
            datetime(2030, 3, 17, tzinfo=UTC) + timedelta(seconds=index),
            f"integration-{index}",
            "INTEGRATION",
        )
        for index, digit in enumerate([digit for _ in range(250) for digit in (9, 0)])
    )
    engine = DerivDigitShadowEngine()
    engine.ingest_history("R_100", ticks=ticks)
    projection = next(item for item in engine.projections() if item.strategy_id is strategy_id)
    counts = tuple(
        sum(int(tick.quote.as_tuple().digits[-1]) == digit for tick in ticks) for digit in range(10)
    )
    percentages = tuple(Decimal(count) * Decimal(100) / Decimal(500) for count in counts)
    return DerivTelemetrySnapshot(
        DerivTelemetrySource.DEMO_LIVE,
        "DEMO",
        True,
        None,
        None,
        None,
        DigitFrequencySnapshot("R_100", 500, counts, percentages, 0),
        (projection,),
    )


def test_deriv_live_trade_settles_atomically(tmp_path: Path) -> None:
    writer, reader, ledger, gate = _services(tmp_path / "state.db")
    transport = FakeDerivTransport(FakeDerivScenario.BUY_SETTLE_WIN, demo_authenticated=True)
    session = DerivLiveOrderSession(transport, "VRTC123456")
    coordinator = OrderCoordinator(writer, session, gate, risk_ledger=ledger)
    processor = BrokerEventProcessor(writer, reader, gate, ledger)

    persisted = coordinator.submit(_request())
    _settle_queued_events(session, processor)

    order = reader.one("orders", "order_id", persisted.order_id)
    assert order is not None
    assert order["state"] == OrderState.SETTLED.value
    assert order["realized_pnl_minor"] == 950
    assert reader.list_by_state("risk_reservations", "ACTIVE") == []
    assert reader.daily_realized_pnl_by_currency(
        since_utc=datetime.now(UTC) - timedelta(minutes=1)
    ) == {"USD": 950}
    assert reader.financial_effect_counts(persisted.order_id) == {
        "pnl_application_count": 1,
        "reservation_release_count": 1,
    }


def test_safe_stop_blocks_entries_but_preserves_open_settlement(tmp_path: Path) -> None:
    writer, reader, ledger, gate = _services(tmp_path / "state.db")
    transport = FakeDerivTransport(FakeDerivScenario.BUY_SETTLE_WIN, demo_authenticated=True)
    session = DerivLiveOrderSession(transport, "VRTC123456")
    coordinator = OrderCoordinator(writer, session, gate, risk_ledger=ledger)
    processor = BrokerEventProcessor(writer, reader, gate, ledger)
    persisted = coordinator.submit(_request())

    gate.block("HG_SAFE_STOP")
    with pytest.raises(RuntimeError, match="Health Gate blocked"):
        coordinator.submit(_request())
    _settle_queued_events(session, processor)

    order = reader.one("orders", "order_id", persisted.order_id)
    assert order is not None and order["state"] == OrderState.SETTLED.value
    assert order["realized_pnl_minor"] == 950


def test_digit_diff_one_tick_flow_preserves_prediction_in_outbox(tmp_path: Path) -> None:
    writer, reader, ledger, gate = _services(tmp_path / "state.db")
    transport = FakeDerivTransport(FakeDerivScenario.BUY_SETTLE_WIN, demo_authenticated=True)
    session = DerivLiveOrderSession(transport, "VRTC123456")
    coordinator = OrderCoordinator(writer, session, gate, risk_ledger=ledger)
    processor = BrokerEventProcessor(writer, reader, gate, ledger)
    request = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITDIFF",
        symbol="R_100",
        direction=Direction.CALL,
        amount=Money(100, "USD"),
        strategy_id="digit-diff-one-tick",
        strategy_version="1.0.0",
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        duration=1,
        duration_unit="t",
        prediction_digit=5,
    )

    persisted = coordinator.submit(request)
    outbox = reader.one("outbox_messages", "message_id", persisted.message_id)
    assert outbox is not None
    assert json.loads(str(outbox["payload"]))["prediction_digit"] == 5
    assert transport.operation_counts[DerivOperation.BUY] == 1
    _settle_queued_events(session, processor)

    order = reader.one("orders", "order_id", persisted.order_id)
    assert order is not None and order["state"] == OrderState.SETTLED.value
    assert order["realized_pnl_minor"] == 95


@pytest.mark.parametrize(
    "product,barrier",
    [
        ("DIGITOVER", 2),
        ("DIGITUNDER", 7),
        ("DIGITDIFF", 5),
        ("DIGITEVEN", None),
        ("DIGITODD", None),
    ],
)
def test_all_demo_digit_contract_families_settle_through_the_same_core_path(
    tmp_path: Path,
    product: str,
    barrier: int | None,
) -> None:
    writer, reader, ledger, gate = _services(tmp_path / f"{product}.db")
    transport = FakeDerivTransport(FakeDerivScenario.BUY_SETTLE_WIN, demo_authenticated=True)
    session = DerivLiveOrderSession(transport, "VRTC123456")
    coordinator = OrderCoordinator(writer, session, gate, risk_ledger=ledger)
    processor = BrokerEventProcessor(writer, reader, gate, ledger)
    request = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product=product,
        symbol="R_100",
        direction=Direction.CALL,
        amount=Money(100, "USD"),
        strategy_id="digit-demo-validation",
        strategy_version="1.9.6",
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        duration=1,
        duration_unit="t",
        prediction_digit=barrier,
    )

    persisted = coordinator.submit(request)
    _settle_queued_events(session, processor)

    order = reader.one("orders", "order_id", persisted.order_id)
    assert order is not None
    assert order["state"] == OrderState.SETTLED.value
    assert order["realized_pnl_minor"] == 95


@pytest.mark.parametrize(
    "strategy_id,expected_product",
    [
        (DerivDigitStrategyId.TAIL_PROBABILITY_EDGE, "DIGITOVER"),
        (DerivDigitStrategyId.SELECTIVE_DIFFERS_EDGE, "DIGITDIFF"),
        (DerivDigitStrategyId.PARITY_REGIME_EDGE, "DIGITODD"),
    ],
)
def test_each_digit_strategy_reaches_demo_settlement_through_application_path(
    tmp_path: Path,
    strategy_id: DerivDigitStrategyId,
    expected_product: str,
) -> None:
    writer, reader, ledger, gate = _services(tmp_path / f"{strategy_id.value}.db")
    assert ledger.update_digit_risk_config(
        replace(
            ledger.digit_config,
            auto_select_symbol=False,
            active_strategy_id=strategy_id.value,
        ),
        gate,
    ) == (True, None)
    transport = FakeDerivTransport(FakeDerivScenario.BUY_SETTLE_WIN, demo_authenticated=True)
    session = DerivLiveOrderSession(transport, "VRTC123456")
    coordinator = OrderCoordinator(writer, session, gate, risk_ledger=ledger)
    processor = BrokerEventProcessor(writer, reader, gate, ledger)
    runtime = _AutoTraderRuntime(coordinator, reader, ledger, gate)
    snapshot = _digit_signal_snapshot(strategy_id)
    trader = DerivDigitAutoTrader(
        runtime,  # type: ignore[arg-type]
        "VRTC123456",
        lambda: snapshot,
    )

    assert trader.evaluate_once() is True
    assert trader.last_reason == "BOT_ORDER_SUBMITTED"
    assert transport.operation_counts[DerivOperation.BUY] == 1
    _settle_queued_events(session, processor)

    deadline = time.monotonic() + 1.0
    summaries = reader.ui_order_summaries(limit=10)
    while (
        summaries
        and summaries[0]["state"] != OrderState.SETTLED.value
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
        _settle_queued_events(session, processor)
        summaries = reader.ui_order_summaries(limit=10)
    assert len(summaries) == 1
    assert summaries[0]["state"] == OrderState.SETTLED.value
    settled = reader.list_by_state("orders", OrderState.SETTLED.value)
    assert len(settled) == 1
    intent = reader.one("trade_intents", "intent_id", str(settled[0]["intent_id"]))
    assert intent is not None
    assert intent["strategy_id"] == strategy_id.value
    assert intent["product"] == expected_product


@pytest.mark.parametrize(
    "strategy_id",
    [
        DerivDigitStrategyId.TAIL_PROBABILITY_EDGE,
        DerivDigitStrategyId.SELECTIVE_DIFFERS_EDGE,
        DerivDigitStrategyId.PARITY_REGIME_EDGE,
    ],
)
def test_each_digit_strategy_uses_next_martingale_stake_after_demo_loss(
    tmp_path: Path,
    strategy_id: DerivDigitStrategyId,
) -> None:
    writer, reader, ledger, gate = _services(tmp_path / f"martingale-{strategy_id.value}.db")
    config = replace(
        ledger.digit_config,
        martingale_enabled=True,
        martingale_multiplier=Decimal("2.00"),
        martingale_max_steps=2,
        martingale_max_stake_minor_units=400,
        max_consecutive_losses=3,
        daily_stop_loss_minor_units=1000,
        auto_select_symbol=False,
        active_strategy_id=strategy_id.value,
    )
    assert ledger.update_digit_risk_config(config, gate) == (True, None)
    transport = FakeDerivTransport(FakeDerivScenario.BUY_SETTLE_LOSS, demo_authenticated=True)
    session = DerivLiveOrderSession(transport, "VRTC123456")
    coordinator = OrderCoordinator(writer, session, gate, risk_ledger=ledger)
    processor = BrokerEventProcessor(writer, reader, gate, ledger)
    runtime = _AutoTraderRuntime(coordinator, reader, ledger, gate)
    snapshot = _digit_signal_snapshot(strategy_id)

    first = DerivDigitAutoTrader(runtime, "VRTC123456", lambda: snapshot)  # type: ignore[arg-type]
    assert first.evaluate_once() is True
    _settle_queued_events(session, processor)
    assert ledger.get_digit_metrics().martingale_step == 1
    assert ledger.get_digit_metrics().next_stake_minor_units == 200

    second = DerivDigitAutoTrader(runtime, "VRTC123456", lambda: snapshot)  # type: ignore[arg-type]
    assert second.evaluate_once() is True
    amounts = [int(item["amount_minor"]) for item in reversed(reader.ui_order_summaries(limit=10))]
    assert amounts == [100, 200]


def test_martingale_progression_is_durable_idempotent_and_asset_pinned(
    tmp_path: Path,
) -> None:
    database = tmp_path / "durable-martingale.db"
    writer, reader, ledger, gate = _services(database)
    config = replace(
        ledger.digit_config,
        martingale_enabled=True,
        martingale_multiplier=Decimal("2.00"),
        martingale_max_steps=2,
        martingale_max_stake_minor_units=400,
        max_consecutive_losses=3,
        daily_stop_loss_minor_units=1000,
        auto_select_symbol=False,
    )
    assert ledger.update_digit_risk_config(config, gate) == (True, None)
    loss_transport = FakeDerivTransport(
        FakeDerivScenario.BUY_SETTLE_LOSS,
        demo_authenticated=True,
    )
    loss_session = DerivLiveOrderSession(loss_transport, "VRTC123456")
    coordinator = OrderCoordinator(writer, loss_session, gate, risk_ledger=ledger)
    processor = BrokerEventProcessor(writer, reader, gate, ledger)
    request = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC123456",
        product="DIGITDIFF",
        symbol="R_100",
        direction=Direction.CALL,
        amount=Money(100, "USD"),
        strategy_id="selective-differs-edge",
        strategy_version="durability-test",
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        duration=1,
        duration_unit="t",
        prediction_digit=5,
    )
    coordinator.submit(request)
    loss_session.drain_contract_events(timeout=0)
    settlement = None
    event = loss_session.next_queued_event(timeout=0)
    while event is not None:
        processor.process(event)
        if event.external_status.value == "SETTLED":
            settlement = event
        event = loss_session.next_queued_event(timeout=0)
    assert settlement is not None
    assert processor.process(settlement).status is BrokerEventApplyStatus.DUPLICATE
    assert reader.digit_risk_runtime() is not None
    assert ledger.get_digit_metrics().martingale_step == 1
    assert ledger.get_digit_metrics().recovery_symbol == "R_100"
    assert ledger.get_digit_metrics().cumulative_sequence_loss_minor_units == 100

    # A later status query may rediscover the same settled contract with a new
    # evidence identifier. It must resolve the attempt without advancing risk
    # state a second time.
    attempt_id = str(uuid4())
    writer.begin_reconciliation_attempt(
        attempt_id,
        settlement.client_order_ref,
        settlement.correlation_id,
    )
    writer.apply_reconciliation_evidence(
        attempt_id,
        ReconciliationEvidence(
            evidence_id=str(uuid4()),
            source=ReconciliationSource.STATUS_QUERY,
            observed_at=datetime.now(UTC),
            client_order_ref=settlement.client_order_ref,
            broker_order_id=settlement.broker_order_id,
            external_status=ExternalOrderStatus.SETTLED,
            broker=settlement.broker,
            account_id=settlement.account_id,
            product=settlement.product,
            symbol=settlement.symbol,
            direction=settlement.direction,
            amount=settlement.amount,
            evidence_version=1,
            realized_pnl_minor=settlement.result_minor,
            raw_reference_hash=f"late-status-{settlement.evidence_hash}",
        ),
    )
    durable_after_late_reconciliation = reader.digit_risk_runtime()
    assert durable_after_late_reconciliation is not None
    assert durable_after_late_reconciliation["martingale_step"] == 1
    assert durable_after_late_reconciliation["daily_pnl_minor"] == -100
    writer.close()

    # Simulate a complete Core restart: no in-memory progression is reused.
    writer2 = SingleDatabaseWriter(database)
    reader2 = StateReader(database)
    ledger2 = RiskLedger(digit_config=config)
    gate2 = HealthGate()
    processor2 = BrokerEventProcessor(writer2, reader2, gate2, ledger2)
    restored = ledger2.get_digit_metrics()
    assert restored.martingale_step == 1
    assert restored.next_stake_minor_units == 200
    assert restored.recovery_symbol == "R_100"
    assert restored.daily_pnl_minor_units == -100

    # A second natural loss must advance the durable sequence from step 1 to
    # step 2 using the restored stake, without losing the pinned asset.
    second_loss_transport = FakeDerivTransport(
        FakeDerivScenario.BUY_SETTLE_LOSS,
        demo_authenticated=True,
    )
    second_loss_session = DerivLiveOrderSession(second_loss_transport, "VRTC123456")
    second_coordinator = OrderCoordinator(
        writer2,
        second_loss_session,
        gate2,
        risk_ledger=ledger2,
    )
    second_coordinator.submit(
        replace(
            request,
            correlation_id=str(uuid4()),
            amount=Money(200, "USD"),
            deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        )
    )
    _settle_queued_events(second_loss_session, processor2)
    after_second_loss = ledger2.get_digit_metrics()
    assert after_second_loss.martingale_step == 2
    assert after_second_loss.next_stake_minor_units == 400
    assert after_second_loss.recovery_symbol == "R_100"
    assert after_second_loss.cumulative_sequence_loss_minor_units == 300
    writer2.close()

    # A second complete Core restart restores step 2. A subsequent win then
    # resets step, pin and accumulated sequence loss exactly once.
    writer3 = SingleDatabaseWriter(database)
    reader3 = StateReader(database)
    ledger3 = RiskLedger(digit_config=config)
    gate3 = HealthGate()
    processor3 = BrokerEventProcessor(writer3, reader3, gate3, ledger3)
    restored_again = ledger3.get_digit_metrics()
    assert restored_again.martingale_step == 2
    assert restored_again.next_stake_minor_units == 400
    assert restored_again.recovery_symbol == "R_100"
    assert restored_again.cumulative_sequence_loss_minor_units == 300

    win_transport = FakeDerivTransport(
        FakeDerivScenario.BUY_SETTLE_WIN,
        demo_authenticated=True,
    )
    win_session = DerivLiveOrderSession(win_transport, "VRTC123456")
    win_coordinator = OrderCoordinator(writer3, win_session, gate3, risk_ledger=ledger3)
    win_coordinator.submit(
        replace(
            request,
            correlation_id=str(uuid4()),
            amount=Money(400, "USD"),
            deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        )
    )
    _settle_queued_events(win_session, processor3)
    after_win = ledger3.get_digit_metrics()
    assert after_win.martingale_step == 0
    assert after_win.next_stake_minor_units == 100
    assert after_win.recovery_symbol is None
    assert after_win.cumulative_sequence_loss_minor_units == 0
    writer3.close()
