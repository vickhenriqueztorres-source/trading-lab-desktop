"""Circuit Breaker thread-safe por (broker, operation) para a camada de resiliência.

Protege contra tempestades de chamadas a serviços indisponíveis e falhas em cascata.
Diferencia estritamente falhas de infraestrutura (rede, timeout, 5xx, manutenção)
de erros normais de usuário/negócio (saldo insuficiente, símbolo fechado),
não abrindo o circuito para erros de usuário.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from apps.core.broker_resilience.models import (
    BrokerCircuitOpenError,
    BrokerName,
    CircuitState,
    ErrorCategory,
)

DEFAULT_CIRCUIT_THRESHOLD: Final[int] = 5
DEFAULT_CIRCUIT_WINDOW_SECONDS: Final[float] = 60.0
DEFAULT_CIRCUIT_RECOVERY_SECONDS: Final[float] = 30.0

# Erros de negócio/usuário que NUNCA devem penalizar a integridade do circuito
_NON_CIRCUIT_CATEGORIES: Final[frozenset[ErrorCategory]] = frozenset(
    {
        ErrorCategory.INVALID_SYMBOL,
        ErrorCategory.INSUFFICIENT_FUNDS,
        ErrorCategory.INVALID_REQUEST,
        ErrorCategory.RESPONSE_SCHEMA,
    }
)


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    """Visão pontual e imutável do estado de um circuit breaker."""

    broker: BrokerName
    operation: str
    state: CircuitState
    failure_count: int
    last_failure_time: float | None
    remaining_recovery_seconds: float | None


class SingleCircuitBreaker:
    """Circuito individual associado a uma chave específica (broker, operation)."""

    def __init__(
        self,
        broker: BrokerName,
        operation: str,
        *,
        failure_threshold: int = DEFAULT_CIRCUIT_THRESHOLD,
        failure_window_seconds: float = DEFAULT_CIRCUIT_WINDOW_SECONDS,
        recovery_timeout: float = DEFAULT_CIRCUIT_RECOVERY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold deve ser positivo")
        if failure_window_seconds <= 0 or recovery_timeout <= 0:
            raise ValueError("janelas de tempo devem ser positivas")

        self.broker = broker
        self.operation = operation
        self.failure_threshold = failure_threshold
        self.failure_window = failure_window_seconds
        self.recovery_timeout = recovery_timeout
        self._clock = clock

        self._lock = threading.RLock()
        self._state = CircuitState.CLOSED
        self._failures: deque[float] = deque()
        self._last_failure_time: float | None = None
        self._half_open_in_flight = False

    def _evaluate_state(self, now: float) -> None:
        """Transita de OPEN para HALF_OPEN se o período de cooldown tiver expirado."""
        if self._state is CircuitState.OPEN and self._last_failure_time is not None:
            elapsed = now - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_in_flight = False

    def check_call(self) -> None:
        """Verifica se a chamada pode prosseguir ou levanta BrokerCircuitOpenError."""
        with self._lock:
            now = self._clock()
            self._evaluate_state(now)

            if self._state is CircuitState.OPEN:
                remaining = 0.0
                if self._last_failure_time is not None:
                    remaining = max(0.0, self.recovery_timeout - (now - self._last_failure_time))
                raise BrokerCircuitOpenError(
                    broker=self.broker,
                    operation=self.operation,
                    remaining_seconds=remaining,
                )

            if self._state is CircuitState.HALF_OPEN:
                if self._half_open_in_flight:
                    raise BrokerCircuitOpenError(
                        broker=self.broker,
                        operation=self.operation,
                        remaining_seconds=0.0,
                        message=(
                            f"Circuit Breaker para {self.broker.value}:{self.operation} "
                            "está HALF_OPEN e uma requisição de teste já está em andamento."
                        ),
                    )
                self._half_open_in_flight = True

    def record_success(self) -> None:
        """Registra êxito; zera contadores e restabelece circuito para CLOSED."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures.clear()
            self._half_open_in_flight = False

    def record_failure(self, category: ErrorCategory | None = None) -> None:
        """Registra falha se for de infraestrutura; abre circuito se atingir limiar."""
        if category in _NON_CIRCUIT_CATEGORIES:
            # Erro de usuário/regra de negócio não degrada a integridade do circuito
            return

        with self._lock:
            now = self._clock()
            self._last_failure_time = now
            self._half_open_in_flight = False

            # Se falhou durante o teste em HALF_OPEN, reabre imediatamente
            if self._state is CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                return

            # Janela deslizante de falhas
            self._failures.append(now)
            cutoff = now - self.failure_window
            while self._failures and self._failures[0] < cutoff:
                self._failures.popleft()

            if len(self._failures) >= self.failure_threshold:
                self._state = CircuitState.OPEN

    def snapshot(self) -> CircuitSnapshot:
        """Gera snapshot thread-safe do estado atual."""
        with self._lock:
            now = self._clock()
            self._evaluate_state(now)
            remaining: float | None = None
            if self._state is CircuitState.OPEN and self._last_failure_time is not None:
                remaining = max(0.0, self.recovery_timeout - (now - self._last_failure_time))

            return CircuitSnapshot(
                broker=self.broker,
                operation=self.operation,
                state=self._state,
                failure_count=len(self._failures),
                last_failure_time=self._last_failure_time,
                remaining_recovery_seconds=remaining,
            )


class BrokerCircuitBreakerRegistry:
    """Registro e orquestrador global de Circuit Breakers por corretora e operação."""

    def __init__(
        self,
        *,
        failure_threshold: int | None = None,
        failure_window_seconds: float | None = None,
        recovery_timeout: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        env_threshold = int(
            os.getenv("BROKER_CIRCUIT_FAILURE_THRESHOLD", str(DEFAULT_CIRCUIT_THRESHOLD))
        )
        env_window = float(
            os.getenv("BROKER_CIRCUIT_FAILURE_WINDOW_SECONDS", str(DEFAULT_CIRCUIT_WINDOW_SECONDS))
        )
        env_recovery = float(
            os.getenv("BROKER_CIRCUIT_RECOVERY_SECONDS", str(DEFAULT_CIRCUIT_RECOVERY_SECONDS))
        )

        self.failure_threshold = (
            failure_threshold if failure_threshold is not None else env_threshold
        )
        self.failure_window = (
            failure_window_seconds if failure_window_seconds is not None else env_window
        )
        self.recovery_timeout = recovery_timeout if recovery_timeout is not None else env_recovery
        self._clock = clock

        self._breakers: dict[tuple[BrokerName, str], SingleCircuitBreaker] = {}
        self._lock = threading.Lock()

    def _get(self, broker: BrokerName | str, operation: str) -> SingleCircuitBreaker:
        b_name = BrokerName.from_value(broker)
        op_clean = str(operation).strip().lower()
        key = (b_name, op_clean)

        with self._lock:
            cb = self._breakers.get(key)
            if cb is None:
                cb = SingleCircuitBreaker(
                    broker=b_name,
                    operation=op_clean,
                    failure_threshold=self.failure_threshold,
                    failure_window_seconds=self.failure_window,
                    recovery_timeout=self.recovery_timeout,
                    clock=self._clock,
                )
                self._breakers[key] = cb
            return cb

    def check_call(self, broker: BrokerName | str, operation: str) -> None:
        """Valida disponibilidade de chamada ou levanta BrokerCircuitOpenError."""
        self._get(broker, operation).check_call()

    def record_success(self, broker: BrokerName | str, operation: str) -> None:
        """Notifica execução bem-sucedida."""
        self._get(broker, operation).record_success()

    def record_failure(
        self,
        broker: BrokerName | str,
        operation: str,
        category: ErrorCategory | None = None,
    ) -> None:
        """Notifica ocorrência de erro."""
        self._get(broker, operation).record_failure(category)

    def snapshot(self, broker: BrokerName | str, operation: str) -> CircuitSnapshot:
        """Retorna snapshot de um circuito específico."""
        return self._get(broker, operation).snapshot()

    def list_snapshots(self) -> list[CircuitSnapshot]:
        """Retorna lista de snapshots de todos os circuitos ativos."""
        with self._lock:
            breakers = list(self._breakers.values())
        return [b.snapshot() for b in breakers]
