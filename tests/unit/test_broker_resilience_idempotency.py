"""Testes unitários para o rastreador de idempotência e marcação de ordens UNKNOWN."""

from __future__ import annotations

import pytest

from apps.core.broker_resilience.idempotency import (
    IdempotencyState,
    IdempotencyTracker,
    create_idempotency_key,
)
from apps.core.broker_resilience.models import BrokerName


def test_create_idempotency_key_deterministic() -> None:
    """Valida determinismo da chave para mesmo broker, operação e trade_id."""
    k1 = create_idempotency_key(BrokerName.DERIV, "buy", "trade-1")
    k2 = create_idempotency_key(BrokerName.DERIV, "buy", "trade-1")
    k3 = create_idempotency_key(BrokerName.DERIV, "buy", "trade-2")

    assert k1 == k2
    assert k1 != k3
    assert "deriv-buy" in k1


def test_idempotency_lifecycle_success() -> None:
    """Valida fluxo normal: start -> PENDING -> mark_confirmed -> CONFIRMED."""
    tracker = IdempotencyTracker()
    key = create_idempotency_key(BrokerName.IQOPTION, "place_order", "ord-1")

    allowed, _ = tracker.can_dispatch(key)
    assert allowed is True

    record = tracker.start_attempt(key, BrokerName.IQOPTION, "place_order", "ord-1")
    assert record.state is IdempotencyState.PENDING
    assert record.attempts == 1

    tracker.mark_confirmed(key, {"order_id": 98765})
    rec = tracker.get_record(key)
    assert rec is not None
    assert rec.state is IdempotencyState.CONFIRMED

    # Não deve permitir novo despacho para ordem confirmada
    can_dispatch, reason = tracker.can_dispatch(key)
    assert can_dispatch is False
    assert reason == "ALREADY_CONFIRMED"


def test_idempotency_timeout_marks_unknown_and_demands_reconciliation() -> None:
    """REGRA CRÍTICA: Timeout em ordem mutante transita para UNKNOWN e impede novas ordens."""
    tracker = IdempotencyTracker()
    key = create_idempotency_key(BrokerName.DERIV, "buy", "trade-xyz")

    tracker.start_attempt(key, BrokerName.DERIV, "buy", "trade-xyz")
    # Ocorre timeout na rede
    tracker.mark_unknown(key, reason="SOCKET_TIMEOUT_AFTER_SEND")

    rec = tracker.get_record(key)
    assert rec is not None
    assert rec.state is IdempotencyState.UNKNOWN

    # Despacho bloqueado
    allowed, reason = tracker.can_dispatch(key)
    assert allowed is False
    assert reason == "ORDER_UNKNOWN_PENDING_RECONCILIATION"

    # Tentativa de reiniciar a chave em estado UNKNOWN deve levantar erro fatal
    with pytest.raises(RuntimeError) as exc_info:
        tracker.start_attempt(key, BrokerName.DERIV, "buy", "trade-xyz")
    assert "Reconciliação é obrigatória" in str(exc_info.value)

    # Reconciliação resolve o caso
    tracker.mark_reconciled(key, final_outcome="SETTLED_WON")
    rec_reconciled = tracker.get_record(key)
    assert rec_reconciled is not None
    assert rec_reconciled.state is IdempotencyState.RECONCILED
