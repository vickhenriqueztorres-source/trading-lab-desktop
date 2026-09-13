"""Limitador de taxa (Rate Limiter) thread-safe para operações de broker.

Implementa o algoritmo Token Bucket por par (broker, operation), protegendo as APIs
contra penalidades de 429 e respeitando os limites estritos da Deriv e IQ Option.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Final

from apps.core.broker_resilience.models import BrokerName

DEFAULT_DERIV_RPM: Final[int] = 60
DEFAULT_IQOPTION_RPM: Final[int] = 30
DEFAULT_BURST: Final[int] = 5


class TokenBucket:
    """Bucket de tokens thread-safe com recarga contínua proporcional ao tempo."""

    def __init__(
        self,
        rate_per_second: float,
        burst_capacity: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second deve ser maior que zero")
        if burst_capacity <= 0:
            raise ValueError("burst_capacity deve ser maior que zero")

        self.rate = float(rate_per_second)
        self.capacity = float(burst_capacity)
        self._clock = clock
        self._tokens = float(burst_capacity)
        self._last_refill = self._clock()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + (elapsed * self.rate))
            self._last_refill = now

    def acquire(self, timeout_seconds: float | None = None) -> bool:
        """Tenta consumir 1 token. Retorna True se bem-sucedido, False se esgotado/expirado."""
        deadline = (self._clock() + timeout_seconds) if timeout_seconds is not None else None

        while True:
            with self._lock:
                now = self._clock()
                self._refill(now)

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True

                # Se for verificação imediata (sem timeout ou timeout zero)
                if deadline is None or timeout_seconds == 0:
                    return False

                remaining = deadline - now
                if remaining <= 0:
                    return False

                # Tempo necessário para recarregar até 1 token
                deficit = 1.0 - self._tokens
                needed_seconds = deficit / self.rate
                sleep_duration = min(remaining, needed_seconds)

            # Dormir fora do lock para não bloquear outras threads
            time.sleep(max(0.001, sleep_duration))

    def retry_after_seconds(self) -> float:
        """Retorna o tempo estimado em segundos até haver ao menos 1 token disponível."""
        with self._lock:
            now = self._clock()
            self._refill(now)
            if self._tokens >= 1.0:
                return 0.0
            deficit = 1.0 - self._tokens
            return max(0.0, deficit / self.rate)


class BrokerRateLimiter:
    """Gerenciador centralizado de Rate Limiting por corretora e operação."""

    def __init__(
        self,
        *,
        deriv_rpm: int | None = None,
        iqoption_rpm: int | None = None,
        burst: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Lê de argumentos ou de variáveis de ambiente com defaults seguros
        env_deriv = int(os.getenv("DERIV_REQUESTS_PER_MINUTE", str(DEFAULT_DERIV_RPM)))
        env_iq = int(os.getenv("IQOPTION_REQUESTS_PER_MINUTE", str(DEFAULT_IQOPTION_RPM)))
        env_burst = int(os.getenv("BROKER_RATE_LIMIT_BURST", str(DEFAULT_BURST)))

        self._rpm: dict[BrokerName, int] = {
            BrokerName.DERIV: max(1, deriv_rpm if deriv_rpm is not None else env_deriv),
            BrokerName.IQOPTION: max(1, iqoption_rpm if iqoption_rpm is not None else env_iq),
            BrokerName.UNKNOWN: 10,
        }
        self._burst = max(1, burst if burst is not None else env_burst)
        self._clock = clock
        self._buckets: dict[tuple[BrokerName, str], TokenBucket] = {}
        self._lock = threading.Lock()

    def _get_bucket(self, broker: BrokerName | str, operation: str) -> TokenBucket:
        b_name = BrokerName.from_value(broker)
        op_clean = str(operation).strip().lower()
        key = (b_name, op_clean)

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                rpm = self._rpm.get(b_name, 30)
                rate_per_sec = rpm / 60.0
                bucket = TokenBucket(
                    rate_per_second=rate_per_sec,
                    burst_capacity=self._burst,
                    clock=self._clock,
                )
                self._buckets[key] = bucket
            return bucket

    def acquire(
        self,
        broker: BrokerName | str,
        operation: str,
        timeout_seconds: float | None = None,
    ) -> bool:
        """Tenta adquirir permissão para chamada. Retorna False se exceder o limite no prazo."""
        bucket = self._get_bucket(broker, operation)
        return bucket.acquire(timeout_seconds=timeout_seconds)

    def retry_after_seconds(self, broker: BrokerName | str, operation: str) -> float:
        """Informa quanto tempo esperar até que a operação seja liberada."""
        bucket = self._get_bucket(broker, operation)
        return bucket.retry_after_seconds()

    def reset(self, broker: BrokerName | str | None = None, operation: str | None = None) -> None:
        """Limpa buckets para testes ou recuperação de ambiente."""
        with self._lock:
            if broker is None and operation is None:
                self._buckets.clear()
            else:
                b_name = BrokerName.from_value(broker) if broker else None
                op_clean = operation.strip().lower() if operation else None
                keys_to_del = [
                    k
                    for k in self._buckets
                    if (b_name is None or k[0] == b_name) and (op_clean is None or k[1] == op_clean)
                ]
                for k in keys_to_del:
                    del self._buckets[k]
