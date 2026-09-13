"""Camada aditiva de resiliência para operações de Deriv e IQ Option.

Oferece classificação de erros, rate limiting, circuit breaker, validação de schemas
e políticas de retry/reconciliação seguras, sem quebrar retrocompatibilidade.
"""

from __future__ import annotations

from apps.core.broker_resilience.circuit_breaker import (
    BrokerCircuitBreakerRegistry,
    CircuitSnapshot,
    SingleCircuitBreaker,
)
from apps.core.broker_resilience.classifier import (
    BrokerErrorClassifier,
    sanitize_sensitive_text,
)
from apps.core.broker_resilience.idempotency import (
    IdempotencyRecord,
    IdempotencyState,
    IdempotencyTracker,
    create_idempotency_key,
)
from apps.core.broker_resilience.models import (
    MUTATING_OPERATIONS,
    Action,
    BrokerCircuitOpenError,
    BrokerErrorContext,
    BrokerName,
    BrokerRateLimitExceededError,
    BrokerResilienceError,
    BrokerResponseSchemaError,
    CircuitState,
    ErrorCategory,
    ErrorDecision,
)
from apps.core.broker_resilience.policies import (
    BrokerPolicyEngine,
    calculate_backoff_delay,
)
from apps.core.broker_resilience.rate_limiter import (
    BrokerRateLimiter,
    TokenBucket,
)
from apps.core.broker_resilience.response_validator import (
    BrokerResponseValidator,
    ValidatedResponse,
)
from apps.core.broker_resilience.service import BrokerResilienceService

__all__ = [
    "Action",
    "BrokerCircuitBreakerRegistry",
    "BrokerCircuitOpenError",
    "BrokerErrorClassifier",
    "BrokerErrorContext",
    "BrokerName",
    "BrokerPolicyEngine",
    "BrokerRateLimitExceededError",
    "BrokerRateLimiter",
    "BrokerResilienceError",
    "BrokerResilienceService",
    "BrokerResponseSchemaError",
    "BrokerResponseValidator",
    "CircuitSnapshot",
    "CircuitState",
    "ErrorCategory",
    "ErrorDecision",
    "IdempotencyRecord",
    "IdempotencyState",
    "IdempotencyTracker",
    "MUTATING_OPERATIONS",
    "SingleCircuitBreaker",
    "TokenBucket",
    "ValidatedResponse",
    "calculate_backoff_delay",
    "create_idempotency_key",
    "sanitize_sensitive_text",
]
