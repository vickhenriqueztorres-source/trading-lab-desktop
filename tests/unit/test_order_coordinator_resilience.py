from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from apps.core.broker_resilience import (
    BrokerName,
    BrokerResilienceService,
    CircuitState,
)
from apps.core.coordinator import OrderCoordinator
from apps.core.health import HealthGate
from apps.core.worker_client import DeliveryCertainty, OrderSubmissionPort, WorkerDispatchError
from apps.simulated_worker.worker import SimulatedWorker
from packages.domain.models import Broker, OrderCommand, OrderRequest, WorkerOutcome, utc_now
from packages.persistence.reader import StateReader
from packages.persistence.writer import SingleDatabaseWriter
from packages.protocol.errors import ProtocolErrorCode


def test_coordinator_with_resilience_disabled_is_transparent_bypass(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    worker = SimulatedWorker([WorkerOutcome.ACCEPTED])
    gate = HealthGate()
    resilience = BrokerResilienceService(enabled=False)

    coordinator = OrderCoordinator(writer, worker, gate, resilience_service=resilience)
    persisted = coordinator.submit(order_request)

    assert persisted.order_id is not None
    assert coordinator.resilience_service.enabled is False


def test_coordinator_submit_command_directly(tmp_path: Path, order_request: OrderRequest) -> None:
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    worker = SimulatedWorker([WorkerOutcome.ACCEPTED])
    gate = HealthGate()

    coordinator = OrderCoordinator(writer, worker, gate)
    cmd = OrderCommand(
        message_id="msg-1",
        correlation_id="corr-1",
        intent_id="int-1",
        order_id="ord-1",
        broker=Broker.DERIV,
        account_id="CR12345",
        product="DIGIT_MATCHES",
        symbol="R_100",
        direction=order_request.direction,
        amount=order_request.amount,
        deadline_at=utc_now(),
    )
    result = coordinator.submit(cmd)
    assert isinstance(result, OrderCommand)
    assert result.message_id == "msg-1"


def test_coordinator_circuit_open_blocks_and_records_not_sent(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    worker = MagicMock(spec=OrderSubmissionPort)
    gate = HealthGate()
    resilience = BrokerResilienceService(enabled=True)

    # Forcar o Circuit Breaker a abrir para DERIV:submit_order
    cb = resilience.circuit_breaker._get(BrokerName.DERIV, "submit_order")
    cb._state = CircuitState.OPEN
    cb._last_failure_time = time.monotonic()

    coordinator = OrderCoordinator(writer, worker, gate, resilience_service=resilience)
    persisted = coordinator.submit(order_request)

    assert persisted is not None
    # Worker nao deve ter sido chamado devido ao Circuit Breaker aberto
    worker.submit_order.assert_not_called()

    # Verificar no banco que a mensagem foi marcada como NOT_SENT com razao BROKER_CIRCUIT_OPEN
    reader = StateReader(db_path)
    outbox = reader.outbox_for_intent(persisted.intent_id)
    assert outbox is not None
    assert outbox["state"] == "BLOCKED_NOT_SENT"
    assert outbox["state_reason"] == "BROKER_CIRCUIT_OPEN"


def test_coordinator_on_success_updates_resilience(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    worker = SimulatedWorker([WorkerOutcome.ACCEPTED])
    gate = HealthGate()
    resilience = BrokerResilienceService(enabled=True)

    coordinator = OrderCoordinator(writer, worker, gate, resilience_service=resilience)
    coordinator.submit(order_request)

    # Verifica se o circuit breaker registrou o sucesso
    snap = resilience.circuit_breaker.snapshot(BrokerName.DERIV, "submit_order")
    assert snap.failure_count == 0
    assert snap.state is CircuitState.CLOSED


def test_coordinator_on_worker_error_updates_resilience(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    worker = MagicMock(spec=OrderSubmissionPort)
    worker.submit_order.side_effect = WorkerDispatchError(
        ProtocolErrorCode.WORKER_CRASHED,
        DeliveryCertainty.POSSIBLY_SENT,
        "Worker crashed",
    )
    gate = HealthGate()
    resilience = BrokerResilienceService(enabled=True)

    coordinator = OrderCoordinator(writer, worker, gate, resilience_service=resilience)
    coordinator.submit(order_request)

    # Falha deve ter sido registrada no circuit breaker
    snap = resilience.circuit_breaker.snapshot(BrokerName.DERIV, "submit_order")
    assert snap.failure_count == 1

    # HealthGate deve ter sido bloqueado com HG_ORDER_UNKNOWN por ser POSSIBLY_SENT
    allowed, blocker = gate.can_enter_order(order_request.broker.value, order_request.account_id)
    assert not allowed
    assert blocker == "HG_ORDER_UNKNOWN"


def test_coordinator_swapped_arguments_backward_compat(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    worker = SimulatedWorker([WorkerOutcome.ACCEPTED])
    gate = HealthGate()

    # Passa health_gate antes de worker
    coord = OrderCoordinator(writer, gate, worker)
    assert coord._health_gate is gate
    assert coord._dispatcher._worker is worker


def test_timeout_after_possible_send_never_dispatches_twice(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    """Teste CRÍTICO: Timeout em POSSIBLY_SENT não deve reenviar ordem."""
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    worker = MagicMock(spec=OrderSubmissionPort)
    worker.submit_order.side_effect = WorkerDispatchError(
        ProtocolErrorCode.ORDER_DISPATCH_AMBIGUOUS,
        DeliveryCertainty.POSSIBLY_SENT,
        "Worker timed out",
    )
    gate = HealthGate()
    resilience = BrokerResilienceService(enabled=True)

    coordinator = OrderCoordinator(writer, worker, gate, resilience_service=resilience)
    coordinator.submit(order_request)

    assert worker.submit_order.call_count == 1, (
        f"Worker foi chamado {worker.submit_order.call_count} vezes — RISCO DE ORDEM DUPLICADA!"
    )


def test_health_gate_remains_authoritative(tmp_path: Path, order_request: OrderRequest) -> None:
    """HealthGate deve bloquear ANTES da resiliência e do worker."""
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    worker = MagicMock(spec=OrderSubmissionPort)
    gate = HealthGate()
    gate.block_scope(order_request.broker.value, order_request.account_id, "HG_RISK_LIMIT_EXCEEDED")
    resilience = BrokerResilienceService(enabled=True)

    coordinator = OrderCoordinator(writer, worker, gate, resilience_service=resilience)
    with pytest.raises(RuntimeError, match="Health Gate blocked"):
        coordinator.submit(order_request)

    worker.submit_order.assert_not_called()


def test_circuit_open_does_not_create_external_dispatch(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    """Circuit open não deve gerar nenhuma chamada de rede/worker."""
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    worker = MagicMock(spec=OrderSubmissionPort)
    gate = HealthGate()
    resilience = BrokerResilienceService(enabled=True)
    resilience.circuit_breaker._get(BrokerName.DERIV, "submit_order")._state = CircuitState.OPEN
    resilience.circuit_breaker._get(
        BrokerName.DERIV, "submit_order"
    )._last_failure_time = time.monotonic()

    coordinator = OrderCoordinator(writer, worker, gate, resilience_service=resilience)
    coordinator.submit(order_request)

    worker.submit_order.assert_not_called()
    reader = StateReader(db_path)
    outbox = reader.list_by_state("outbox_messages", "BLOCKED_NOT_SENT")
    assert len(outbox) == 1
    assert outbox[0]["state_reason"] == "BROKER_CIRCUIT_OPEN"


def test_feature_flag_off_has_same_dispatch_behavior_as_before(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    """Com flag desligada, comportamento deve ser IDÊNTICO ao anterior."""
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    worker = SimulatedWorker([WorkerOutcome.ACCEPTED])
    gate = HealthGate()
    resilience = BrokerResilienceService(enabled=False)
    # Mesmo forçando circuito aberto, com flag desabilitada deve agir como bypass
    resilience.circuit_breaker._get(BrokerName.DERIV, "submit_order")._state = CircuitState.OPEN

    coordinator = OrderCoordinator(writer, worker, gate, resilience_service=resilience)
    persisted = coordinator.submit(order_request)
    assert persisted.order_id is not None
    reader = StateReader(db_path)
    outbox = reader.outbox_for_intent(persisted.intent_id)
    assert outbox is not None
    assert outbox["state"] == "DISPATCHED"


def test_not_sent_and_possibly_sent_have_different_persistence_states(
    tmp_path: Path, order_request: OrderRequest
) -> None:
    """NOT_SENT e POSSIBLY_SENT devem ter estados diferentes no banco.
    (BLOCKED_NOT_SENT vs AMBIGUOUS).
    """
    db_path = tmp_path / "state.db"
    writer = SingleDatabaseWriter(db_path)
    worker = MagicMock(spec=OrderSubmissionPort)
    gate = HealthGate()
    coordinator = OrderCoordinator(writer, worker, gate)

    # Cenário 1: NOT_SENT
    worker.submit_order.side_effect = WorkerDispatchError(
        ProtocolErrorCode.WORKER_NOT_READY,
        DeliveryCertainty.NOT_SENT,
        "Worker not ready",
    )
    p1 = coordinator.submit(order_request)
    reader = StateReader(db_path)
    out1 = reader.outbox_for_intent(p1.intent_id)
    assert out1 is not None
    assert out1["state"] == "BLOCKED_NOT_SENT"
    assert out1["state_reason"] == "WORKER_NOT_READY"

    # Cenário 2: POSSIBLY_SENT (usando novo gate desimpedido)
    gate2 = HealthGate()
    coord2 = OrderCoordinator(writer, worker, gate2)
    worker.submit_order.side_effect = WorkerDispatchError(
        ProtocolErrorCode.ORDER_DISPATCH_AMBIGUOUS,
        DeliveryCertainty.POSSIBLY_SENT,
        "Ambiguous timeout",
    )
    req2 = replace(
        order_request,
        correlation_id="corr-second-attempt",
        account_id="demo-account-2",
    )
    p2 = coord2.submit(req2)
    out2 = reader.outbox_for_intent(p2.intent_id)
    assert out2 is not None
    assert out2["state"] == "AMBIGUOUS"
    assert out2["state_reason"] == "ORDER_DISPATCH_AMBIGUOUS"


def test_rate_limit_does_not_block_order_reconciliation() -> None:
    """Rate limiter não pode bloquear reconciliação de ordem."""
    resilience = BrokerResilienceService(enabled=True)
    # Esgota rate limiter para submit_order
    for _ in range(100):
        resilience.rate_limiter.acquire(BrokerName.DERIV, "submit_order", timeout_seconds=0.0)

    # Outra operação (ex: consulta/reconciliação) continua liberada
    assert (
        resilience.rate_limiter.acquire(BrokerName.DERIV, "query_order_status", timeout_seconds=0.0)
        is True
    )
