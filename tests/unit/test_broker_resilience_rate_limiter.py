"""Testes unitários para o Rate Limiter de brokers."""

from __future__ import annotations

import concurrent.futures

from apps.core.broker_resilience.models import BrokerName
from apps.core.broker_resilience.rate_limiter import BrokerRateLimiter, TokenBucket


class ControlledClock:
    """Relógio determinístico para testes temporais."""

    def __init__(self, start: float = 1000.0) -> None:
        self.time = start

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


def test_token_bucket_burst_capacity_and_exhaustion() -> None:
    """Valida capacidade de burst e bloqueio imediato após esgotamento."""
    clock = ControlledClock()
    # 60 RPM = 1 token/sec, burst = 3
    bucket = TokenBucket(rate_per_second=1.0, burst_capacity=3.0, clock=clock)

    # 3 aquisições imediatas devem funcionar (burst)
    assert bucket.acquire() is True
    assert bucket.acquire() is True
    assert bucket.acquire() is True

    # 4ª aquisição sem espera deve falhar
    assert bucket.acquire(timeout_seconds=0) is False
    assert bucket.retry_after_seconds() > 0.0

    # Avança 1 segundo -> 1 token recarregado
    clock.advance(1.0)
    assert bucket.acquire() is True
    assert bucket.acquire(timeout_seconds=0) is False


def test_broker_rate_limiter_respects_broker_quotas() -> None:
    """Valida isolamento de quotas entre Deriv e IQ Option."""
    clock = ControlledClock()
    limiter = BrokerRateLimiter(deriv_rpm=60, iqoption_rpm=30, burst=2, clock=clock)

    # Consome burst da Deriv
    assert limiter.acquire(BrokerName.DERIV, "proposal") is True
    assert limiter.acquire(BrokerName.DERIV, "proposal") is True
    assert limiter.acquire(BrokerName.DERIV, "proposal", timeout_seconds=0) is False

    # IQ Option para outra operação permanece com seu bucket intacto
    assert limiter.acquire(BrokerName.IQOPTION, "market_history") is True
    assert limiter.acquire(BrokerName.IQOPTION, "market_history") is True
    assert limiter.acquire(BrokerName.IQOPTION, "market_history", timeout_seconds=0) is False


def test_broker_rate_limiter_concurrency() -> None:
    """Valida aquisição thread-safe concorrente sem race conditions."""
    clock = ControlledClock()
    limiter = BrokerRateLimiter(deriv_rpm=6000, iqoption_rpm=6000, burst=100, clock=clock)

    acquired_count = 0

    def worker() -> bool:
        return limiter.acquire(BrokerName.DERIV, "test_op", timeout_seconds=0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker) for _ in range(100)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
        acquired_count = sum(results)

    # Exatamente 100 aquisições bem-sucedidas devido ao burst de 100
    assert acquired_count == 100
    # O 101 deve falhar imediatamente
    assert limiter.acquire(BrokerName.DERIV, "test_op", timeout_seconds=0) is False
