"""Testes unitários para as políticas e modelos de Broker Resilience."""

from __future__ import annotations

from apps.core.broker_resilience.models import (
    Action,
    BrokerErrorContext,
    BrokerName,
    ErrorCategory,
)
from apps.core.broker_resilience.policies import (
    BrokerPolicyEngine,
    calculate_backoff_delay,
)


def test_calculate_backoff_delay_exponential() -> None:
    """Valida o cálculo de backoff exponencial com teto máximo."""
    assert calculate_backoff_delay(0, base_delay=1.0, max_delay=10.0) == 1.0
    assert calculate_backoff_delay(1, base_delay=1.0, max_delay=10.0) == 2.0
    assert calculate_backoff_delay(2, base_delay=1.0, max_delay=10.0) == 4.0
    assert calculate_backoff_delay(3, base_delay=1.0, max_delay=10.0) == 8.0
    assert calculate_backoff_delay(4, base_delay=1.0, max_delay=10.0) == 10.0  # limit capped


def test_calculate_backoff_respects_retry_after() -> None:
    """Valida que retry_after explícito é respeitado até o teto."""
    assert calculate_backoff_delay(0, retry_after=5.5, max_delay=10.0) == 5.5
    assert calculate_backoff_delay(0, retry_after=15.0, max_delay=10.0) == 10.0


def test_mutating_operation_timeout_forces_order_unknown_and_reconcile() -> None:
    """REGRA CRÍTICA: Timeout em compra/venda NUNCA autoriza retry e aciona reconciliação."""
    engine = BrokerPolicyEngine(max_read_retries=3)
    ctx = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="buy",
        trade_id="trade-123",
        retry_count=0,
    )
    assert ctx.is_mutating is True

    decision = engine.evaluate(ctx, ErrorCategory.TIMEOUT)
    assert decision.category is ErrorCategory.ORDER_UNKNOWN
    assert decision.retry_allowed is False
    assert decision.max_retries == 0
    assert decision.should_reconcile_order is True
    assert Action.RECONCILE in decision.actions
    assert Action.ABORT in decision.actions
    assert Action.RETRY not in decision.actions


def test_mutating_operation_network_disconnect_forces_reconcile() -> None:
    """Queda de rede em ordem mutante (place_order) nunca autoriza retry cego."""
    engine = BrokerPolicyEngine()
    ctx = BrokerErrorContext(
        broker=BrokerName.IQOPTION,
        operation="place_order",
        trade_id="iq-ord-456",
        retry_count=0,
    )
    assert ctx.is_mutating is True

    decision = engine.evaluate(ctx, ErrorCategory.NETWORK)
    assert decision.category is ErrorCategory.ORDER_UNKNOWN
    assert decision.retry_allowed is False
    assert decision.should_reconcile_order is True
    assert Action.RECONCILE in decision.actions


def test_read_operation_timeout_allows_limited_retries() -> None:
    """Operações de consulta permitem retry limitado com backoff."""
    engine = BrokerPolicyEngine(max_read_retries=2)
    ctx_attempt_0 = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="proposal",
        retry_count=0,
    )
    assert ctx_attempt_0.is_mutating is False

    d0 = engine.evaluate(ctx_attempt_0, ErrorCategory.TIMEOUT)
    assert d0.retry_allowed is True
    assert Action.RETRY in d0.actions
    assert d0.retry_after_seconds is not None
    assert d0.should_open_circuit is False

    # Quando atinge o limite máximo de retries
    ctx_attempt_2 = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="proposal",
        retry_count=2,
    )
    d2 = engine.evaluate(ctx_attempt_2, ErrorCategory.TIMEOUT)
    assert d2.retry_allowed is False
    assert Action.ABORT in d2.actions
    assert Action.OPEN_CIRCUIT in d2.actions
    assert d2.should_open_circuit is True


def test_auth_failure_never_retries_and_demands_reauthentication() -> None:
    """Falha 401/403 nunca retenta ordem e exige reautenticação."""
    engine = BrokerPolicyEngine()
    ctx = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="buy",
        http_status=401,
    )
    decision = engine.evaluate(ctx, ErrorCategory.AUTH)
    assert decision.retry_allowed is False
    assert Action.REAUTHENTICATE in decision.actions
    assert Action.ABORT in decision.actions
    assert Action.NOTIFY in decision.actions
    assert Action.RETRY not in decision.actions


def test_maintenance_opens_circuit_and_aborts() -> None:
    """Manutenção de broker abre circuit breaker e notifica operador."""
    engine = BrokerPolicyEngine()
    ctx = BrokerErrorContext(
        broker=BrokerName.IQOPTION,
        operation="market_history",
    )
    decision = engine.evaluate(ctx, ErrorCategory.MAINTENANCE)
    assert decision.retry_allowed is False
    assert decision.should_open_circuit is True
    assert Action.OPEN_CIRCUIT in decision.actions
    assert Action.NOTIFY in decision.actions


def test_user_errors_do_not_open_circuit() -> None:
    """Erros de usuário (símbolo inválido, saldo) abortam sem abrir circuit breaker."""
    engine = BrokerPolicyEngine()
    ctx_symbol = BrokerErrorContext(broker=BrokerName.IQOPTION, operation="place_order")
    d_symbol = engine.evaluate(ctx_symbol, ErrorCategory.INVALID_SYMBOL)
    assert d_symbol.should_open_circuit is False
    assert d_symbol.retry_allowed is False

    ctx_funds = BrokerErrorContext(broker=BrokerName.DERIV, operation="buy")
    d_funds = engine.evaluate(ctx_funds, ErrorCategory.INSUFFICIENT_FUNDS)
    assert d_funds.should_open_circuit is False
    assert d_funds.retry_allowed is False


def test_unknown_error_requires_manual_review() -> None:
    """Erro não categorizado devolve decisão conservadora de revisão manual."""
    engine = BrokerPolicyEngine()
    ctx = BrokerErrorContext(broker=BrokerName.DERIV, operation="custom_call")
    decision = engine.evaluate(ctx, ErrorCategory.UNKNOWN)
    assert decision.retry_allowed is False
    assert Action.MANUAL_REVIEW in decision.actions
    assert Action.ABORT in decision.actions
