"""Testes unitários para a fachada BrokerResilienceService."""

from __future__ import annotations

import pytest

from apps.core.broker_resilience.circuit_breaker import BrokerCircuitBreakerRegistry
from apps.core.broker_resilience.idempotency import (
    IdempotencyState,
    IdempotencyTracker,
    create_idempotency_key,
)
from apps.core.broker_resilience.models import (
    Action,
    BrokerCircuitOpenError,
    BrokerErrorContext,
    BrokerName,
    BrokerRateLimitExceededError,
    ErrorCategory,
)
from apps.core.broker_resilience.rate_limiter import BrokerRateLimiter
from apps.core.broker_resilience.service import BrokerResilienceService


class ControlledClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.time = start

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


def test_service_disabled_by_default_bypasses_all_checks() -> None:
    """Quando desabilitada (default), a fachada atua como bypass transparente."""
    service = BrokerResilienceService(enabled=False)
    assert service.enabled is False

    # before_call não levanta exceção
    service.before_call(BrokerName.DERIV, "proposal")

    # on_success retorna None e não interfere
    assert service.on_success(BrokerName.DERIV, "proposal", None, {}) is None

    # can_retry retorna False
    ctx = BrokerErrorContext(broker=BrokerName.DERIV, operation="proposal")
    decision = service.on_error(ctx)
    assert service.can_retry(ctx, decision) is False


def test_service_before_call_blocks_on_rate_limit_when_enabled() -> None:
    """Quando habilitada, estouro de taxa bloqueia com BrokerRateLimitExceededError."""
    clock = ControlledClock()
    rate_limiter = BrokerRateLimiter(deriv_rpm=60, burst=1, clock=clock)
    service = BrokerResilienceService(enabled=True, rate_limiter=rate_limiter)

    # 1ª chamada consome o burst de 1
    service.before_call(BrokerName.DERIV, "proposal")

    # 2ª chamada imediata sem recarga levanta erro
    with pytest.raises(BrokerRateLimitExceededError) as exc_info:
        service.before_call(BrokerName.DERIV, "proposal")
    assert exc_info.value.retry_after_seconds > 0.0


def test_service_before_call_blocks_on_circuit_open_when_enabled() -> None:
    """Quando habilitada, circuit breaker aberto impede novas chamadas."""
    clock = ControlledClock()
    circuit_breaker = BrokerCircuitBreakerRegistry(failure_threshold=1, clock=clock)
    service = BrokerResilienceService(enabled=True, circuit_breaker=circuit_breaker)

    # Registra falha que atinge threshold 1
    ctx = BrokerErrorContext(broker=BrokerName.DERIV, operation="quote", retry_count=0)
    service.on_error(ctx, RuntimeError("Deriv server timeout"))

    with pytest.raises(BrokerCircuitOpenError):
        service.before_call(BrokerName.DERIV, "quote")


def test_service_mutating_operation_error_flow() -> None:
    """REGRA CRÍTICA: Erro em buy/place_order aciona ORDER_UNKNOWN + RECONCILE e bloqueia retry."""
    idempotency = IdempotencyTracker()
    service = BrokerResilienceService(enabled=True, idempotency=idempotency)

    key = create_idempotency_key(BrokerName.DERIV, "buy", "order-abc")
    ctx = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="buy",
        idempotency_key=key,
        trade_id="order-abc",
        retry_count=0,
    )
    assert ctx.is_mutating is True

    # Registra início
    service.before_call(BrokerName.DERIV, "buy", ctx)

    # Simula timeout após envio
    decision = service.on_error(ctx, TimeoutError("Socket timed out after sending order command"))

    assert decision.category is ErrorCategory.ORDER_UNKNOWN
    assert decision.retry_allowed is False
    assert decision.should_reconcile_order is True
    assert Action.RECONCILE in decision.actions

    # can_retry deve ser rigorosamente False para qualquer operação mutante
    assert service.can_retry(ctx, decision) is False

    # Chave no tracker de idempotência deve estar como UNKNOWN
    rec = idempotency.get_record(key)
    assert rec is not None
    assert rec.state is IdempotencyState.UNKNOWN

    # Nova tentativa antes de reconciliação é rejeitada
    with pytest.raises(RuntimeError):
        service.before_call(BrokerName.DERIV, "buy", ctx)


def test_service_read_operation_success_and_retry_flow() -> None:
    """Fluxo normal de leitura com sucesso validado e retry autorizado em falha transitória."""
    service = BrokerResilienceService(enabled=True)

    ctx = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="proposal",
        retry_count=0,
    )

    # Sucesso com payload correto
    valid_payload = {
        "proposal": {
            "id": "prop-111",
            "ask_price": 2.50,
            "payout": 4.80,
        }
    }
    validated = service.on_success(BrokerName.DERIV, "proposal", ctx, valid_payload)
    assert validated is not None
    assert validated.data["proposal"]["id"] == "prop-111"

    # Falha transitória em leitura permite retry
    transient_decision = service.on_error(ctx, TimeoutError("Read timeout"))
    assert service.can_retry(ctx, transient_decision) is True
