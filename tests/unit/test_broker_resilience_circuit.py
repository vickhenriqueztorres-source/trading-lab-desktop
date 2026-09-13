"""Testes unitários para o Circuit Breaker de brokers."""

from __future__ import annotations

import pytest

from apps.core.broker_resilience.circuit_breaker import (
    BrokerCircuitBreakerRegistry,
    SingleCircuitBreaker,
)
from apps.core.broker_resilience.models import (
    BrokerCircuitOpenError,
    BrokerName,
    CircuitState,
    ErrorCategory,
)


class ControlledClock:
    """Relógio determinístico para testes temporais."""

    def __init__(self, start: float = 1000.0) -> None:
        self.time = start

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


def test_circuit_breaker_opens_after_threshold_and_recovers() -> None:
    """Valida o ciclo: CLOSED -> 5 falhas -> OPEN -> cooldown -> HALF_OPEN -> CLOSED."""
    clock = ControlledClock()
    cb = SingleCircuitBreaker(
        broker=BrokerName.DERIV,
        operation="proposal",
        failure_threshold=5,
        failure_window_seconds=60.0,
        recovery_timeout=30.0,
        clock=clock,
    )

    assert cb.snapshot().state is CircuitState.CLOSED

    # 4 falhas de infraestrutura não abrem ainda
    for _ in range(4):
        cb.record_failure(ErrorCategory.TIMEOUT)
        cb.check_call()  # deve passar sem levantar exceção

    assert cb.snapshot().state is CircuitState.CLOSED

    # 5ª falha abre o circuito
    cb.record_failure(ErrorCategory.TIMEOUT)
    assert cb.snapshot().state is CircuitState.OPEN

    # Quando OPEN, chamadas são bloqueadas com BrokerCircuitOpenError
    with pytest.raises(BrokerCircuitOpenError) as exc_info:
        cb.check_call()
    assert exc_info.value.remaining_seconds is not None
    assert exc_info.value.remaining_seconds > 0.0

    # Avança tempo mas não o suficiente (20s de 30s)
    clock.advance(20.0)
    with pytest.raises(BrokerCircuitOpenError):
        cb.check_call()

    # Avança restante até completar 30s -> transita para HALF_OPEN
    clock.advance(10.0)
    assert cb.snapshot().state is CircuitState.HALF_OPEN

    # 1ª chamada de teste (probe) passa
    cb.check_call()

    # 2ª chamada concorrente durante probe deve ser rejeitada
    with pytest.raises(BrokerCircuitOpenError) as exc_probe:
        cb.check_call()
    assert "HALF_OPEN" in str(exc_probe.value)

    # Sucesso na prova fecha o circuito
    cb.record_success()
    assert cb.snapshot().state is CircuitState.CLOSED
    cb.check_call()  # agora livre


def test_circuit_breaker_reopens_on_probe_failure() -> None:
    """Valida que falha durante estado HALF_OPEN reabre o circuito imediatamente."""
    clock = ControlledClock()
    cb = SingleCircuitBreaker(
        broker=BrokerName.IQOPTION,
        operation="market_history",
        failure_threshold=2,
        failure_window_seconds=60.0,
        recovery_timeout=20.0,
        clock=clock,
    )

    cb.record_failure(ErrorCategory.NETWORK)
    cb.record_failure(ErrorCategory.NETWORK)
    assert cb.snapshot().state is CircuitState.OPEN

    clock.advance(20.0)
    assert cb.snapshot().state is CircuitState.HALF_OPEN

    # Falha na prova
    cb.record_failure(ErrorCategory.TIMEOUT)
    assert cb.snapshot().state is CircuitState.OPEN


def test_user_and_business_errors_do_not_affect_circuit() -> None:
    """Valida que erros de usuário (símbolo inválido, saldo insuficiente) NÃO abrem o circuito."""
    clock = ControlledClock()
    cb = SingleCircuitBreaker(
        broker=BrokerName.DERIV,
        operation="buy",
        failure_threshold=3,
        clock=clock,
    )

    # 10 falhas de saldo insuficiente e símbolo inválido
    for _ in range(5):
        cb.record_failure(ErrorCategory.INSUFFICIENT_FUNDS)
        cb.record_failure(ErrorCategory.INVALID_SYMBOL)

    assert cb.snapshot().state is CircuitState.CLOSED
    assert cb.snapshot().failure_count == 0
    cb.check_call()  # Permanece completamente liberado


def test_registry_snapshot_and_isolation() -> None:
    """Valida o orquestrador global de breakers e isolamento por broker."""
    clock = ControlledClock()
    registry = BrokerCircuitBreakerRegistry(failure_threshold=2, clock=clock)

    registry.record_failure(BrokerName.DERIV, "proposal", ErrorCategory.TIMEOUT)
    registry.record_failure(BrokerName.DERIV, "proposal", ErrorCategory.TIMEOUT)

    # Deriv:proposal está OPEN
    with pytest.raises(BrokerCircuitOpenError):
        registry.check_call(BrokerName.DERIV, "proposal")

    # IQ Option:market_history está livre (CLOSED)
    registry.check_call(BrokerName.IQOPTION, "market_history")

    snapshots = registry.list_snapshots()
    assert len(snapshots) >= 2
