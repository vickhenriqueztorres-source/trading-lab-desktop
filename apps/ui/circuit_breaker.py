"""Módulo de Circuit Breaker para comunicação resiliente de IPC.

Implementa o padrão Circuit Breaker para proteger chamadas IPC contra falhas em cascata,
isolando workers ou serviços com lentidão ou indisponibilidade repetida.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(StrEnum):
    """Estados do Circuit Breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(RuntimeError):
    """Exceção levantada quando uma chamada é bloqueada devido ao circuito estar aberto."""

    def __init__(
        self,
        message: str = "Circuit Breaker está ABERTO; requisição bloqueada.",
        *,
        remaining_seconds: float | None = None,
    ) -> None:
        self.remaining_seconds = remaining_seconds
        super().__init__(message)


class CircuitBreaker:
    """Implementa o padrão Circuit Breaker thread-safe para chamadas IPC.

    Estados:
        - CLOSED: Operação normal. Todas as requisições passam.
        - OPEN: O número de falhas consecutivas ultrapassou o limiar. Todas as
          chamadas falham imediatamente com CircuitOpenError.
        - HALF_OPEN: O tempo de recuperação expirou. Uma requisição de teste é
          permitida para verificar se o serviço alvo se recuperou. Se tiver sucesso,
          retorna a CLOSED; se falhar, retorna a OPEN.

    Attributes:
        failure_threshold: Quantidade de falhas consecutivas para abrir o circuito.
        recovery_timeout: Tempo em segundos aberto antes de transitar para HALF_OPEN.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        *,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        """Inicializa o CircuitBreaker.

        Args:
            failure_threshold: Número máximo de falhas consecutivas toleradas (padrão: 5).
            recovery_timeout: Tempo de espera em segundos até a recuperação (padrão: 30.0).
            monotonic_clock: Relógio monotônico opcional (para testes determinísticos).
        """
        if failure_threshold <= 0:
            raise ValueError("failure_threshold deve ser maior que zero")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout deve ser maior que zero")

        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = monotonic_clock or time.monotonic

        self._lock = threading.RLock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._half_open_in_flight = False

    @property
    def state(self) -> str:
        """Retorna o estado atual do circuito ('CLOSED', 'OPEN' ou 'HALF_OPEN')."""
        with self._lock:
            self._evaluate_state_transition()
            return self._state.value

    @property
    def failure_count(self) -> int:
        """Retorna a contagem atual de falhas consecutivas."""
        with self._lock:
            return self._failure_count

    @property
    def last_failure_time(self) -> float | None:
        """Retorna o timestamp da última falha registrada ou None."""
        with self._lock:
            return self._last_failure_time

    def _evaluate_state_transition(self) -> None:
        """Atualiza o estado baseado no tempo decorrido desde a última falha."""
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            now = self._clock()
            elapsed = now - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                logger.info(
                    "Circuit Breaker transicionando de OPEN para HALF_OPEN após %.1fs",
                    elapsed,
                )
                self._state = CircuitState.HALF_OPEN
                self._half_open_in_flight = False

    def record_success(self) -> None:
        """Registra uma chamada bem-sucedida, resetando falhas e fechando o circuito."""
        with self._lock:
            if self._state != CircuitState.CLOSED:
                logger.info("Circuit Breaker restabelecido para CLOSED após sucesso na recuperação")
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._half_open_in_flight = False

    def record_failure(self) -> None:
        """Registra falha e abre o circuito se limiar atingido ou se estava HALF_OPEN."""
        with self._lock:
            now = self._clock()
            self._last_failure_time = now
            self._failure_count += 1
            self._half_open_in_flight = False

            if self._state == CircuitState.HALF_OPEN:
                logger.warning("Falha durante estado HALF_OPEN; reabrindo circuito para OPEN")
                self._state = CircuitState.OPEN
            elif self._failure_count >= self.failure_threshold:
                if self._state != CircuitState.OPEN:
                    logger.warning(
                        "Circuit Breaker atingiu o limiar de %d falhas; abrindo circuito para OPEN",
                        self._failure_count,
                    )
                self._state = CircuitState.OPEN

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Executa a função através do Circuit Breaker ou levanta CircuitOpenError.

        Se a função for bem-sucedida, o sucesso é registrado automaticamente.
        Se a função levantar uma exceção, a falha é registrada e a exceção é relançada.

        Args:
            func: Função ou método a ser executado.
            *args: Argumentos posicionais para a função.
            **kwargs: Argumentos nomeados para a função.

        Returns:
            O resultado da chamada de func(*args, **kwargs).

        Raises:
            CircuitOpenError: Se o circuito estiver no estado OPEN.
            Exception: Qualquer exceção gerada pela execução de func.
        """
        with self._lock:
            self._evaluate_state_transition()

            if self._state == CircuitState.OPEN:
                remaining = 0.0
                if self._last_failure_time is not None:
                    elapsed = self._clock() - self._last_failure_time
                    remaining = max(0.0, self.recovery_timeout - elapsed)
                raise CircuitOpenError(
                    f"Circuit Breaker está ABERTO ({remaining:.1f}s restantes para recuperação)",
                    remaining_seconds=remaining,
                )

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_in_flight:
                    raise CircuitOpenError(
                        "Circuit Breaker HALF_OPEN: requisição de teste já está em andamento",
                        remaining_seconds=0.0,
                    )
                self._half_open_in_flight = True

        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise
