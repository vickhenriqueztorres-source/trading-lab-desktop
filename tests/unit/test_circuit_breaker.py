"""Testes unitários para o módulo apps.ui.circuit_breaker."""

from __future__ import annotations

import pytest

from apps.ui.circuit_breaker import CircuitBreaker, CircuitOpenError


class MockClock:
    """Relógio simulado para testes determinísticos de tempo."""

    def __init__(self, initial_time: float = 1000.0) -> None:
        self._time = initial_time

    def __call__(self) -> float:
        return self._time

    def advance(self, seconds: float) -> None:
        self._time += seconds


def test_initial_state_is_closed() -> None:
    """Valida que o estado inicial é CLOSED com zero falhas."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)
    assert cb.state == "CLOSED"
    assert cb.failure_count == 0
    assert cb.last_failure_time is None


def test_successful_call_keeps_closed() -> None:
    """Valida execução bem-sucedida através de call()."""
    cb = CircuitBreaker()
    result = cb.call(lambda x, y: x + y, 2, 3)
    assert result == 5
    assert cb.state == "CLOSED"
    assert cb.failure_count == 0


def test_failures_reach_threshold_and_open_circuit() -> None:
    """Valida que atingir o threshold de falhas consecutivas abre o circuito."""
    clock = MockClock()
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=20.0, monotonic_clock=clock)

    def failing_action() -> None:
        raise ValueError("erro transiente")

    # 1ª falha
    with pytest.raises(ValueError):
        cb.call(failing_action)
    assert cb.state == "CLOSED"
    assert cb.failure_count == 1

    # 2ª falha
    with pytest.raises(ValueError):
        cb.call(failing_action)
    assert cb.state == "CLOSED"
    assert cb.failure_count == 2

    # 3ª falha (atinge threshold)
    with pytest.raises(ValueError):
        cb.call(failing_action)
    assert cb.state == "OPEN"
    assert cb.failure_count == 3
    assert cb.last_failure_time == 1000.0


def test_open_circuit_blocks_subsequent_calls() -> None:
    """Valida que chamadas são bloqueadas com CircuitOpenError quando o circuito está OPEN."""
    clock = MockClock()
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0, monotonic_clock=clock)

    cb.record_failure()
    cb.record_failure()
    assert cb.state == "OPEN"

    with pytest.raises(CircuitOpenError) as exc_info:
        cb.call(lambda: "nao_deve_executar")

    assert "ABERTO" in str(exc_info.value)
    assert exc_info.value.remaining_seconds is not None
    assert exc_info.value.remaining_seconds > 0


def test_transition_to_half_open_after_timeout() -> None:
    """Valida transição de OPEN para HALF_OPEN após decorrido o recovery_timeout."""
    clock = MockClock(100.0)
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0, monotonic_clock=clock)

    cb.record_failure()
    cb.record_failure()
    assert cb.state == "OPEN"

    # Avança tempo mas menos que 30s
    clock.advance(15.0)
    assert cb.state == "OPEN"

    # Avança para completar 30s
    clock.advance(15.0)
    assert cb.state == "HALF_OPEN"


def test_half_open_success_restores_closed() -> None:
    """Valida que sucesso durante HALF_OPEN restaura estado CLOSED e zera falhas."""
    clock = MockClock(100.0)
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0, monotonic_clock=clock)

    cb.record_failure()
    cb.record_failure()
    clock.advance(30.0)
    assert cb.state == "HALF_OPEN"

    result = cb.call(lambda: "recuperado!")
    assert result == "recuperado!"
    assert cb.state == "CLOSED"
    assert cb.failure_count == 0


def test_half_open_failure_reopens_circuit() -> None:
    """Valida que falha durante HALF_OPEN reabre o circuito imediatamente para OPEN."""
    clock = MockClock(100.0)
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0, monotonic_clock=clock)

    cb.record_failure()
    cb.record_failure()
    clock.advance(30.0)
    assert cb.state == "HALF_OPEN"

    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("falhou no teste")))

    assert cb.state == "OPEN"
    assert cb.last_failure_time == 130.0


def test_invalid_parameters_raise_error() -> None:
    """Valida que parâmetros inválidos no construtor disparam ValueError."""
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(recovery_timeout=-1.0)
