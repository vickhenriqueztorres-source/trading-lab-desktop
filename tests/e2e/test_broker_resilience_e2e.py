"""Teste End-to-End (E2E) em Isolamento para a camada Broker Resilience.

Executado pelo Pytest, validando em ambiente isolado:
1. Timeout em leitura vs. timeout em compra/ordem (ORDER_UNKNOWN e bloqueio de retry);
2. Rate Limiting com estouro de burst e 429 com Retry-After;
3. Resposta 5xx de servidor;
4. Circuit Breaker com ciclo completo e imunidade a erros de usuário;
5. Bypass completo quando a feature flag está desabilitada.
"""

from __future__ import annotations

import pytest

from apps.core.broker_resilience import (
    Action,
    BrokerCircuitBreakerRegistry,
    BrokerCircuitOpenError,
    BrokerErrorContext,
    BrokerName,
    BrokerPolicyEngine,
    BrokerRateLimiter,
    BrokerRateLimitExceededError,
    BrokerResilienceService,
    CircuitState,
    ErrorCategory,
    IdempotencyState,
    IdempotencyTracker,
    create_idempotency_key,
)


class SimulatedClock:
    def __init__(self, initial_time: float = 1000.0) -> None:
        self.current = initial_time

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


def test_e2e_timeout_read_vs_mutating() -> None:
    """Valida que timeout em leitura autoriza retry mas em ordem mutante força ORDER_UNKNOWN."""
    clock = SimulatedClock()
    service = BrokerResilienceService(
        enabled=True,
        policy_engine=BrokerPolicyEngine(max_read_retries=2),
        circuit_breaker=BrokerCircuitBreakerRegistry(clock=clock),
        rate_limiter=BrokerRateLimiter(burst=10, clock=clock),
        idempotency=IdempotencyTracker(clock=clock),
    )

    # 1. Leitura: timeout autoriza retry
    read_ctx = BrokerErrorContext(broker=BrokerName.DERIV, operation="proposal", retry_count=0)
    service.before_call(BrokerName.DERIV, "proposal", read_ctx)
    decision_read = service.on_error(read_ctx, TimeoutError("Read timed out"))
    assert decision_read.category is ErrorCategory.TIMEOUT
    assert decision_read.retry_allowed is True
    assert service.can_retry(read_ctx, decision_read) is True

    # 2. Mutação: timeout força ORDER_UNKNOWN e impede retry
    buy_key = create_idempotency_key(BrokerName.DERIV, "buy", "order-test-1")
    buy_ctx = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="buy",
        idempotency_key=buy_key,
        trade_id="order-test-1",
        retry_count=0,
    )
    service.before_call(BrokerName.DERIV, "buy", buy_ctx)
    decision_buy = service.on_error(buy_ctx, TimeoutError("Timeout after send"))
    assert decision_buy.category is ErrorCategory.ORDER_UNKNOWN
    assert decision_buy.retry_allowed is False
    assert decision_buy.should_reconcile_order is True
    assert Action.RECONCILE in decision_buy.actions
    assert service.can_retry(buy_ctx, decision_buy) is False

    # Idempotência bloqueia nova tentativa imediata
    with pytest.raises(RuntimeError):
        service.before_call(BrokerName.DERIV, "buy", buy_ctx)

    # Reconciliador destrava
    service.idempotency.mark_reconciled(buy_key, "EXECUTED")
    rec = service.idempotency.get_record(buy_key)
    assert rec is not None
    assert rec.state is IdempotencyState.RECONCILED


def test_e2e_rate_limiting_and_429_retry_after() -> None:
    """Valida bloqueio de burst e cálculo de Retry-After para HTTP 429."""
    clock = SimulatedClock()
    limiter = BrokerRateLimiter(iqoption_rpm=30, burst=3, clock=clock)
    service = BrokerResilienceService(enabled=True, rate_limiter=limiter)

    # 3 passam
    for _ in range(3):
        service.before_call(BrokerName.IQOPTION, "market_history")

    # 4ª é bloqueada
    with pytest.raises(BrokerRateLimitExceededError) as exc_info:
        service.before_call(BrokerName.IQOPTION, "market_history")
    assert exc_info.value.retry_after_seconds > 0.0

    # Classificação de resposta 429 do servidor com Retry-After
    ctx_429 = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="proposal",
        http_status=429,
        retry_count=0,
    )
    decision = service.classifier.classify(ctx_429, retry_after=4.0)
    assert decision.category is ErrorCategory.RATE_LIMIT
    assert decision.retry_after_seconds == 4.0
    assert Action.WAIT in decision.actions


def test_e2e_server_5xx_read_vs_mutating() -> None:
    """Valida que 5xx em leitura tolera retry mas em mutação vira ORDER_UNKNOWN."""
    service = BrokerResilienceService(
        enabled=True,
        policy_engine=BrokerPolicyEngine(max_read_retries=2),
    )

    ctx_read = BrokerErrorContext(broker=BrokerName.DERIV, operation="proposal", http_status=500)
    d_read = service.on_error(ctx_read)
    assert d_read.category is ErrorCategory.SERVER
    assert d_read.retry_allowed is True

    ctx_buy = BrokerErrorContext(broker=BrokerName.DERIV, operation="buy", http_status=500)
    d_buy = service.on_error(ctx_buy)
    assert d_buy.category is ErrorCategory.ORDER_UNKNOWN
    assert d_buy.retry_allowed is False


def test_e2e_circuit_breaker_complete_cycle() -> None:
    """Valida o ciclo completo do Circuit Breaker: CLOSED -> OPEN -> HALF_OPEN -> CLOSED."""
    clock = SimulatedClock()
    registry = BrokerCircuitBreakerRegistry(
        failure_threshold=2,
        failure_window_seconds=60.0,
        recovery_timeout=20.0,
        clock=clock,
    )
    service = BrokerResilienceService(enabled=True, circuit_breaker=registry)

    ctx = BrokerErrorContext(broker=BrokerName.IQOPTION, operation="market_history")

    # 2 falhas abrem o circuito
    service.on_error(ctx, ConnectionError("Broken pipe"))
    service.on_error(ctx, ConnectionError("Broken pipe"))
    assert registry.snapshot(BrokerName.IQOPTION, "market_history").state is CircuitState.OPEN

    # Fail-fast
    with pytest.raises(BrokerCircuitOpenError):
        service.before_call(BrokerName.IQOPTION, "market_history")

    # Cooldown 20s -> HALF_OPEN
    clock.advance(20.0)
    assert registry.snapshot(BrokerName.IQOPTION, "market_history").state is CircuitState.HALF_OPEN

    # Sonda passa
    service.before_call(BrokerName.IQOPTION, "market_history")

    # Sucesso fecha circuito
    service.on_success(
        BrokerName.IQOPTION,
        "market_history",
        ctx,
        {"candles": [{"from": 1000, "close": 1.2}]},
    )
    assert registry.snapshot(BrokerName.IQOPTION, "market_history").state is CircuitState.CLOSED

    # Erros de saldo não afetam circuito
    for _ in range(5):
        service.on_error(
            BrokerErrorContext(
                broker=BrokerName.DERIV,
                operation="buy",
                broker_code="InsufficientBalance",
            )
        )
    assert registry.snapshot(BrokerName.DERIV, "buy").state is CircuitState.CLOSED


def test_e2e_feature_flag_disabled_bypass() -> None:
    """Valida que quando desligado, o serviço atua como bypass 100% transparente."""
    service = BrokerResilienceService(enabled=False)
    assert service.enabled is False

    service.before_call(BrokerName.DERIV, "any_op")
    assert service.on_success(BrokerName.DERIV, "any_op", None, {}) is None
