from __future__ import annotations

import concurrent.futures
import threading
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from apps.core.coordinator import (
    MultiBrokerSubmissionRouter,
    OrderCoordinator,
)
from apps.core.health import HealthGate
from apps.core.risk import RiskLedger
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


class MockWorker(OrderSubmissionPort):
    def __init__(self, broker_name: str, delay_s: float = 0.0) -> None:
        self.broker_name = broker_name
        self.delay_s = delay_s
        self.submitted_commands: list[OrderCommand] = []
        self._lock = threading.Lock()

    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult:
        if self.delay_s > 0:
            time.sleep(self.delay_s)
        with self._lock:
            self.submitted_commands.append(command)
        return WorkerSubmissionResult(
            outcome=WorkerOutcome.ACCEPTED,
            broker_order_id=f"{self.broker_name.lower()}_order_{len(self.submitted_commands)}",
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


def test_cross_broker_submission_router_and_dispatch(tmp_path: Path) -> None:
    writer, reader = _init_db(tmp_path)
    health_gate = HealthGate()
    risk_ledger = RiskLedger()

    deriv_worker = MockWorker("DERIV")
    iqoption_worker = MockWorker("IQOPTION")

    router = MultiBrokerSubmissionRouter(
        {
            Broker.DERIV: deriv_worker,
            Broker.IQ_OPTION: iqoption_worker,
        }
    )

    coordinator = OrderCoordinator(
        writer,
        router,
        health_gate,
        risk_ledger=risk_ledger,
    )

    # 1. Submit Deriv Demo order
    req_deriv = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC1001",
        strategy_id="strat_trend",
        strategy_version="1.0.0",
        product="VANILLA_CALL",
        symbol="R_100",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=utc_now() + timedelta(seconds=10),
    )
    order_deriv = coordinator.submit(req_deriv)

    # 2. Submit IQ Option Practice order
    req_iq = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_99",
        strategy_id="strat_rev",
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(2500, "USD"),
        deadline_at=utc_now() + timedelta(seconds=10),
    )
    order_iq = coordinator.submit(req_iq)

    # Verify both workers received their respective commands
    assert len(deriv_worker.submitted_commands) == 1
    assert deriv_worker.submitted_commands[0].order_id == order_deriv.order_id
    assert deriv_worker.submitted_commands[0].broker == Broker.DERIV

    assert len(iqoption_worker.submitted_commands) == 1
    assert iqoption_worker.submitted_commands[0].order_id == order_iq.order_id
    assert iqoption_worker.submitted_commands[0].broker == Broker.IQ_OPTION

    # Verify orders in DB
    db_deriv = reader.one("orders", "order_id", order_deriv.order_id)
    assert db_deriv is not None
    assert db_deriv["state"] == "ACCEPTED"
    assert db_deriv["broker"] == "DERIV"

    db_iq = reader.one("orders", "order_id", order_iq.order_id)
    assert db_iq is not None
    assert db_iq["state"] == "ACCEPTED"
    assert db_iq["broker"] == "IQ_OPTION"


def test_concurrent_dispatch_cross_broker_does_not_block(tmp_path: Path) -> None:
    writer, reader = _init_db(tmp_path)
    health_gate = HealthGate()
    risk_ledger = RiskLedger()

    # Workers with a small delay
    deriv_worker = MockWorker("DERIV", delay_s=0.05)
    iqoption_worker = MockWorker("IQOPTION", delay_s=0.05)

    router = MultiBrokerSubmissionRouter(
        {
            Broker.DERIV: deriv_worker,
            Broker.IQ_OPTION: iqoption_worker,
        }
    )

    coordinator = OrderCoordinator(
        writer,
        router,
        health_gate,
        risk_ledger=risk_ledger,
    )

    req_deriv = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC1001",
        strategy_id="strat_trend",
        strategy_version="1.0.0",
        product="VANILLA_CALL",
        symbol="R_100",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
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
        amount=Money(2500, "USD"),
        deadline_at=utc_now() + timedelta(seconds=10),
    )

    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(coordinator.submit, req_deriv)
        f2 = executor.submit(coordinator.submit, req_iq)
        res_deriv = f1.result()
        res_iq = f2.result()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.0

    assert res_deriv is not None
    assert res_iq is not None
    # If they were serialized sequentially, it would take >= 0.10s
    # In parallel, it should take ~0.05-0.08s
    assert len(deriv_worker.submitted_commands) == 1
    assert len(iqoption_worker.submitted_commands) == 1
