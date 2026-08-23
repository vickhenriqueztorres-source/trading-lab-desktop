from __future__ import annotations

import concurrent.futures
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from apps.core.coordinator import (
    MultiBrokerSubmissionRouter,
    OrderCoordinator,
)
from apps.core.health import HealthGate
from apps.core.risk import GlobalRiskConfig, RiskLedger, RiskLimitExceededError
from apps.core.worker_client import OrderSubmissionPort
from packages.domain.models import (
    Broker,
    Direction,
    Money,
    OrderCommand,
    OrderRequest,
    WorkerOutcome,
    utc_now,
)
from packages.persistence.database import open_writer_connection
from packages.persistence.migrations import apply_migrations
from packages.persistence.reader import StateReader
from packages.persistence.writer import SingleDatabaseWriter
from packages.protocol.messages import WorkerSubmissionResult


class SlowWorker(OrderSubmissionPort):
    def __init__(self, broker: Broker) -> None:
        self.broker = broker
        self.submitted_commands: list[OrderCommand] = []

    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult:
        self.submitted_commands.append(command)
        return WorkerSubmissionResult(
            outcome=WorkerOutcome.ACCEPTED,
            broker_order_id=f"{self.broker.value.lower()}_id_{len(self.submitted_commands)}",
            response_message_id=str(uuid4()),
            correlation_id=command.correlation_id,
            causation_id=command.message_id,
        )


def _init_db(tmp_path: Path) -> tuple[SingleDatabaseWriter, StateReader]:
    db_path = tmp_path / "state.db"
    conn = open_writer_connection(db_path)
    apply_migrations(conn)
    conn.close()
    writer = SingleDatabaseWriter(db_path)
    reader = StateReader(db_path)
    return writer, reader


def test_concurrent_global_exposure_limit_enforcement(tmp_path: Path) -> None:
    writer, reader = _init_db(tmp_path)
    # Global limit = $50.00
    config = GlobalRiskConfig(
        global_max_exposure_minor_units=5000,
        max_exposure_per_symbol_minor_units=5000,
    )
    ledger = RiskLedger(config)
    health_gate = HealthGate()

    deriv_worker = SlowWorker(Broker.DERIV)
    iq_worker = SlowWorker(Broker.IQ_OPTION)

    router = MultiBrokerSubmissionRouter(
        {
            Broker.DERIV: deriv_worker,
            Broker.IQ_OPTION: iq_worker,
        }
    )

    coordinator = OrderCoordinator(
        writer,
        router,
        health_gate,
        risk_ledger=ledger,
    )

    # Two requests for $30.00 each -> Total = $60.00 > $50.00 limit
    req_deriv = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC1001",
        strategy_id="strat_trend",
        strategy_version="1.0.0",
        product="VANILLA_CALL",
        symbol="R_100",
        direction=Direction.CALL,
        amount=Money(3000, "USD"),
        deadline_at=utc_now() + timedelta(seconds=10),
    )

    req_iq = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_99",
        strategy_id="strat_rev",
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(3000, "USD"),
        deadline_at=utc_now() + timedelta(seconds=10),
    )

    results = []
    errors = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(coordinator.submit, req_deriv)
        f2 = executor.submit(coordinator.submit, req_iq)
        for f in (f1, f2):
            try:
                res = f.result()
                results.append(res)
            except Exception as exc:
                errors.append(exc)

    # Exactly one succeeded and one failed with RiskLimitExceededError
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], RiskLimitExceededError)
    assert errors[0].reason_code == "HG_GLOBAL_EXPOSURE_EXCEEDED"

    # Verify SQLite active reservations sum <= $50.00
    active_count = reader.count("risk_reservations")
    assert active_count == 1
    active_row = reader.list_by_state("risk_reservations", "ACTIVE")
    assert len(active_row) == 1
    assert active_row[0]["amount_minor"] == 3000


def test_concurrent_symbol_exposure_limit_enforcement(tmp_path: Path) -> None:
    writer, reader = _init_db(tmp_path)
    # Global limit = $100.00, Symbol limit = $30.00
    config = GlobalRiskConfig(
        global_max_exposure_minor_units=10000,
        max_exposure_per_symbol_minor_units=3000,
    )
    ledger = RiskLedger(config)
    health_gate = HealthGate()

    deriv_worker = SlowWorker(Broker.DERIV)
    iq_worker = SlowWorker(Broker.IQ_OPTION)

    router = MultiBrokerSubmissionRouter(
        {
            Broker.DERIV: deriv_worker,
            Broker.IQ_OPTION: iq_worker,
        }
    )

    coordinator = OrderCoordinator(
        writer,
        router,
        health_gate,
        risk_ledger=ledger,
    )

    # Both order on EURUSD: $20.00 each -> Total = $40.00 > $30.00 symbol limit
    req_deriv = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC1001",
        strategy_id="strat_trend",
        strategy_version="1.0.0",
        product="VANILLA_CALL",
        symbol="frxEURUSD",
        direction=Direction.CALL,
        amount=Money(2000, "USD"),
        deadline_at=utc_now() + timedelta(seconds=10),
    )

    req_iq = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_99",
        strategy_id="strat_rev",
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(2000, "USD"),
        deadline_at=utc_now() + timedelta(seconds=10),
    )

    results = []
    errors = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(coordinator.submit, req_deriv)
        f2 = executor.submit(coordinator.submit, req_iq)
        for f in (f1, f2):
            try:
                res = f.result()
                results.append(res)
            except Exception as exc:
                errors.append(exc)

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], RiskLimitExceededError)
    assert errors[0].reason_code == "HG_SYMBOL_EXPOSURE_LIMIT_EXCEEDED"
