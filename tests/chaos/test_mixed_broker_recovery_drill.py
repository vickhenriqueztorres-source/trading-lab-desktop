from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from apps.core.coordinator import (
    MultiBrokerSubmissionRouter,
    OrderCoordinator,
)
from apps.core.health import HealthGate
from apps.core.reconciliation import (
    MultiBrokerStatusRouter,
    ReconciliationCoordinator,
    ReconciliationOutcome,
)
from apps.core.risk import RiskLedger
from apps.core.worker_client import (
    DeliveryCertainty,
    OrderStatusPort,
    OrderSubmissionPort,
    WorkerDispatchError,
)
from packages.domain.models import (
    Broker,
    Direction,
    ExternalOrderStatus,
    Money,
    OrderCommand,
    OrderRequest,
    OrderStatusQuery,
    ReconciliationEvidence,
    ReconciliationSource,
    StatusQueryOutcome,
    WorkerOutcome,
    utc_now,
)
from packages.persistence.database import open_writer_connection
from packages.persistence.migrations import apply_migrations
from packages.persistence.reader import StateReader
from packages.persistence.writer import FinancialUnitOfWork, SingleDatabaseWriter
from packages.protocol.errors import ProtocolErrorCode
from packages.protocol.messages import OrderStatusResult, WorkerSubmissionResult


class ChaosDerivWorker(OrderSubmissionPort, OrderStatusPort):
    def __init__(self) -> None:
        self.submitted_commands: list[OrderCommand] = []
        self.status_queries: list[OrderStatusQuery] = []
        self.should_timeout_submit = False
        self.reconciliation_status: OrderStatusResult | None = None

    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult:
        self.submitted_commands.append(command)
        if self.should_timeout_submit:
            raise TimeoutError("Deriv submission timed out")
        return WorkerSubmissionResult(
            outcome=WorkerOutcome.ACCEPTED,
            broker_order_id=f"deriv_contract_{len(self.submitted_commands)}",
            response_message_id=str(uuid4()),
            correlation_id=command.correlation_id,
            causation_id=command.message_id,
        )

    def query_order_status(self, query: OrderStatusQuery, *, timeout: float) -> OrderStatusResult:
        self.status_queries.append(query)
        if self.reconciliation_status is not None:
            return self.reconciliation_status
        return OrderStatusResult(
            outcome=StatusQueryOutcome.NOT_FOUND,
            evidence=None,
            response_message_id=str(uuid4()),
            correlation_id=query.correlation_id,
            causation_id=query.order_id,
            reason_code=ProtocolErrorCode.RECONCILIATION_NOT_FOUND.value,
        )


class ChaosIQOptionWorker(OrderSubmissionPort, OrderStatusPort):
    def __init__(self) -> None:
        self.submitted_commands: list[OrderCommand] = []
        self.status_queries: list[OrderStatusQuery] = []
        self.fail_not_sent = False
        self.reconciliation_status: OrderStatusResult | None = None

    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult:
        self.submitted_commands.append(command)
        if self.fail_not_sent:
            raise WorkerDispatchError(
                ProtocolErrorCode.WORKER_NOT_READY,
                DeliveryCertainty.NOT_SENT,
                "IQ Option worker connection dropped before send",
            )
        return WorkerSubmissionResult(
            outcome=WorkerOutcome.ACCEPTED,
            broker_order_id=f"iq_option_{len(self.submitted_commands)}",
            response_message_id=str(uuid4()),
            correlation_id=command.correlation_id,
            causation_id=command.message_id,
        )

    def query_order_status(self, query: OrderStatusQuery, *, timeout: float) -> OrderStatusResult:
        self.status_queries.append(query)
        if self.reconciliation_status is not None:
            return self.reconciliation_status
        return OrderStatusResult(
            outcome=StatusQueryOutcome.NOT_FOUND,
            evidence=None,
            response_message_id=str(uuid4()),
            correlation_id=query.correlation_id,
            causation_id=query.order_id,
            reason_code=ProtocolErrorCode.RECONCILIATION_NOT_FOUND.value,
        )


def _init_db(tmp_path: Path) -> tuple[SingleDatabaseWriter, StateReader]:
    db_path = tmp_path / "state.db"
    conn = open_writer_connection(db_path)
    apply_migrations(conn)
    conn.close()
    writer = SingleDatabaseWriter(db_path)
    reader = StateReader(db_path)
    return writer, reader


def test_mixed_broker_failure_isolation(tmp_path: Path) -> None:
    writer, reader = _init_db(tmp_path)
    health_gate = HealthGate()
    risk_ledger = RiskLedger()

    deriv_worker = ChaosDerivWorker()
    iq_worker = ChaosIQOptionWorker()

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
        risk_ledger=risk_ledger,
    )

    # 1. Simulate IQ Option dropping connection on submit
    iq_worker.fail_not_sent = True

    req_iq = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACC",
        strategy_id="strat_rev",
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(2000, "USD"),
        deadline_at=utc_now() + timedelta(seconds=10),
    )
    coordinator.submit(req_iq)

    # IQ Option scope is blocked
    can_iq, reason_iq = health_gate.can_enter_order("IQ_OPTION", "PRACTICE_ACC")
    assert can_iq is False
    assert reason_iq == "HG_WORKER_NOT_READY"

    # 2. Deriv Demo is NOT blocked and can submit orders successfully
    can_deriv, reason_deriv = health_gate.can_enter_order("DERIV", "VRTC1001")
    assert can_deriv is True
    assert reason_deriv is None

    req_deriv = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.DERIV,
        account_id="VRTC1001",
        strategy_id="strat_trend",
        strategy_version="1.0.0",
        product="VANILLA_CALL",
        symbol="R_100",
        direction=Direction.CALL,
        amount=Money(1500, "USD"),
        deadline_at=utc_now() + timedelta(seconds=10),
    )
    order_deriv = coordinator.submit(req_deriv)
    assert order_deriv is not None

    db_deriv = reader.one("orders", "order_id", order_deriv.order_id)
    assert db_deriv is not None
    assert db_deriv["state"] == "ACCEPTED"


def test_mixed_broker_global_safe_stop(tmp_path: Path) -> None:
    writer, reader = _init_db(tmp_path)
    health_gate = HealthGate()
    risk_ledger = RiskLedger()

    deriv_worker = ChaosDerivWorker()
    iq_worker = ChaosIQOptionWorker()

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
        risk_ledger=risk_ledger,
    )

    # Engage Global Safe Stop
    health_gate.block("HG_SAFE_STOP")

    # Both fail closed
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
    with pytest.raises(RuntimeError, match="Health Gate blocked: HG_SAFE_STOP"):
        coordinator.submit(req_deriv)

    req_iq = OrderRequest(
        correlation_id=str(uuid4()),
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_ACC",
        strategy_id="strat_rev",
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=utc_now() + timedelta(seconds=10),
    )
    with pytest.raises(RuntimeError, match="Health Gate blocked: HG_SAFE_STOP"):
        coordinator.submit(req_iq)


def test_mixed_recovery_drill_post_restart(tmp_path: Path) -> None:
    writer, reader = _init_db(tmp_path)
    uow = FinancialUnitOfWork(writer)
    health_gate = HealthGate()

    now = utc_now()

    # 1. Setup Order 1 (Deriv) in UNKNOWN state
    deriv_intent_id = str(uuid4())
    deriv_order_id = str(uuid4())
    deriv_msg_id = str(uuid4())
    deriv_corr_id = str(uuid4())
    deriv_req = OrderRequest(
        correlation_id=deriv_corr_id,
        broker=Broker.DERIV,
        account_id="VRTC1001",
        strategy_id="strat_trend",
        strategy_version="1.0.0",
        product="VANILLA_CALL",
        symbol="R_100",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=now + timedelta(seconds=60),
    )
    deriv_cmd = OrderCommand(
        message_id=deriv_msg_id,
        correlation_id=deriv_corr_id,
        intent_id=deriv_intent_id,
        order_id=deriv_order_id,
        broker=Broker.DERIV,
        account_id="VRTC1001",
        product="VANILLA_CALL",
        symbol="R_100",
        direction=Direction.CALL,
        amount=Money(1000, "USD"),
        deadline_at=now + timedelta(seconds=60),
    )
    uow.persist(
        request=deriv_req,
        command=deriv_cmd,
        intent_id=deriv_intent_id,
        reservation_id=str(uuid4()),
        order_id=deriv_order_id,
        created_at=now,
    )
    # Claim and mark UNKNOWN (e.g. timeout on submit)
    writer.claim_next_message()
    writer.record_dispatch_result(deriv_cmd, "TIMEOUT_AFTER_POSSIBLE_SEND", broker_order_id=None)

    # 2. Setup Order 2 (IQ Option) in OPEN state
    iq_intent_id = str(uuid4())
    iq_order_id = str(uuid4())
    iq_msg_id = str(uuid4())
    iq_corr_id = str(uuid4())
    iq_req = OrderRequest(
        correlation_id=iq_corr_id,
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_99",
        strategy_id="strat_rev",
        strategy_version="1.0.0",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(2000, "USD"),
        deadline_at=now + timedelta(seconds=60),
    )
    iq_cmd = OrderCommand(
        message_id=iq_msg_id,
        correlation_id=iq_corr_id,
        intent_id=iq_intent_id,
        order_id=iq_order_id,
        broker=Broker.IQ_OPTION,
        account_id="PRACTICE_99",
        product="BINARY_OPTION",
        symbol="EURUSD",
        direction=Direction.CALL,
        amount=Money(2000, "USD"),
        deadline_at=now + timedelta(seconds=60),
    )
    uow.persist(
        request=iq_req,
        command=iq_cmd,
        intent_id=iq_intent_id,
        reservation_id=str(uuid4()),
        order_id=iq_order_id,
        created_at=now,
    )
    writer.claim_next_message()
    writer.record_dispatch_result(iq_cmd, "ACCEPTED", broker_order_id="iq_contract_888")

    # Set up mock workers for reconciliation
    deriv_worker = ChaosDerivWorker()
    deriv_worker.reconciliation_status = OrderStatusResult(
        outcome=StatusQueryOutcome.FOUND,
        evidence=ReconciliationEvidence(
            evidence_id=str(uuid4()),
            source=ReconciliationSource.STATUS_QUERY,
            observed_at=now,
            client_order_ref=deriv_order_id,
            broker_order_id="deriv_contract_777",
            external_status=ExternalOrderStatus.SETTLED,
            broker=Broker.DERIV,
            account_id="VRTC1001",
            product="VANILLA_CALL",
            symbol="R_100",
            direction=Direction.CALL,
            amount=Money(1000, "USD"),
            evidence_version=1,
            realized_pnl_minor=950,
            raw_reference_hash="hash_deriv_settled",
        ),
        response_message_id=str(uuid4()),
        correlation_id=deriv_corr_id,
        causation_id=deriv_order_id,
    )

    iq_worker = ChaosIQOptionWorker()
    iq_worker.reconciliation_status = OrderStatusResult(
        outcome=StatusQueryOutcome.FOUND,
        evidence=ReconciliationEvidence(
            evidence_id=str(uuid4()),
            source=ReconciliationSource.STATUS_QUERY,
            observed_at=now,
            client_order_ref=iq_order_id,
            broker_order_id="iq_contract_888",
            external_status=ExternalOrderStatus.SETTLED,
            broker=Broker.IQ_OPTION,
            account_id="PRACTICE_99",
            product="BINARY_OPTION",
            symbol="EURUSD",
            direction=Direction.CALL,
            amount=Money(2000, "USD"),
            evidence_version=1,
            realized_pnl_minor=1700,
            raw_reference_hash="hash_iq_settled",
        ),
        response_message_id=str(uuid4()),
        correlation_id=iq_corr_id,
        causation_id=iq_order_id,
    )

    status_router = MultiBrokerStatusRouter(
        {
            Broker.DERIV: deriv_worker,
            Broker.IQ_OPTION: iq_worker,
        }
    )

    # Execute Multi-Broker Reconciliation Coordinator
    reconciliation = ReconciliationCoordinator(
        writer,
        reader,
        status_router,
        health_gate,
    )

    report_by_broker = reconciliation.reconcile_all_brokers()

    # Both brokers reconciled successfully
    assert "DERIV" in report_by_broker
    assert report_by_broker["DERIV"] == [ReconciliationOutcome.RESOLVED]

    assert "IQ_OPTION" in report_by_broker
    assert report_by_broker["IQ_OPTION"] == [ReconciliationOutcome.RESOLVED]

    # Verify orders in DB are SETTLED
    deriv_order = reader.one("orders", "order_id", deriv_order_id)
    assert deriv_order is not None
    assert deriv_order["state"] == "SETTLED"
    assert deriv_order["broker_order_id"] == "deriv_contract_777"

    iq_order = reader.one("orders", "order_id", iq_order_id)
    assert iq_order is not None
    assert iq_order["state"] == "SETTLED"
    assert iq_order["broker_order_id"] == "iq_contract_888"

    # HealthGate has no blockers
    assert health_gate.contains("HG_ORDER_UNKNOWN") is False
    assert health_gate.contains("HG_RECONCILIATION_REQUIRED") is False
