"""Serviço unificado (Fachada) de Resiliência para Deriv e IQ Option.

Atua como ponto de entrada opt-in para rate limiting, circuit breaker, validação
de schemas e políticas de retry/reconciliação, sem executar chamadas de rede
internamente e sem alterar comportamento pré-existente por padrão.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from apps.core.broker_resilience.circuit_breaker import BrokerCircuitBreakerRegistry
from apps.core.broker_resilience.classifier import BrokerErrorClassifier
from apps.core.broker_resilience.idempotency import IdempotencyTracker
from apps.core.broker_resilience.models import (
    BrokerErrorContext,
    BrokerName,
    BrokerRateLimitExceededError,
    ErrorCategory,
    ErrorDecision,
)
from apps.core.broker_resilience.policies import BrokerPolicyEngine
from apps.core.broker_resilience.rate_limiter import BrokerRateLimiter
from apps.core.broker_resilience.response_validator import (
    BrokerResponseValidator,
    ValidatedResponse,
)

logger = logging.getLogger("core.broker_resilience.service")


class BrokerResilienceService:
    """Fachada unificada e thread-safe para resiliência operacional de brokers."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        policy_engine: BrokerPolicyEngine | None = None,
        classifier: BrokerErrorClassifier | None = None,
        rate_limiter: BrokerRateLimiter | None = None,
        circuit_breaker: BrokerCircuitBreakerRegistry | None = None,
        validator: BrokerResponseValidator | None = None,
        idempotency: IdempotencyTracker | None = None,
    ) -> None:
        if enabled is not None:
            self.enabled = enabled
        else:
            env_val = os.getenv("BROKER_RESILIENCE_ENABLED", "false").strip().lower()
            self.enabled = env_val in {"true", "1", "yes", "on"}

        self.policy_engine = policy_engine or BrokerPolicyEngine()
        self.classifier = classifier or BrokerErrorClassifier(self.policy_engine)
        self.rate_limiter = rate_limiter or BrokerRateLimiter()
        self.circuit_breaker = circuit_breaker or BrokerCircuitBreakerRegistry()
        self.validator = validator or BrokerResponseValidator()
        self.idempotency = idempotency or IdempotencyTracker()

    def before_call(
        self,
        broker: BrokerName | str,
        operation: str,
        context: BrokerErrorContext | None = None,
    ) -> None:
        """Verifica circuit breaker, rate limit e idempotência antes da chamada ao worker/broker."""
        if not self.enabled:
            return

        b_name = BrokerName.from_value(broker)
        op_clean = operation.strip().lower()

        try:
            # 1. Checa Circuit Breaker (levanta BrokerCircuitOpenError se aberto)
            self.circuit_breaker.check_call(b_name, op_clean)

            # 2. Checa Rate Limiter
            if not self.rate_limiter.acquire(b_name, op_clean, timeout_seconds=0.0):
                delay = self.rate_limiter.retry_after_seconds(b_name, op_clean)
                raise BrokerRateLimitExceededError(b_name, op_clean, retry_after_seconds=delay)

            # 3. Checa e registra chave de idempotência se informada
            if context is not None and context.idempotency_key:
                allowed, reason = self.idempotency.can_dispatch(context.idempotency_key)
                if not allowed:
                    msg = (
                        f"Chamada bloqueada por idempotência ({reason}) "
                        f"para a chave {context.idempotency_key}"
                    )
                    raise RuntimeError(msg)
                self.idempotency.start_attempt(
                    key=context.idempotency_key,
                    broker=b_name,
                    operation=op_clean,
                    trade_id=context.trade_id,
                )
        except Exception as exc:
            logger.warning(
                "before_call blocked execution on %s:%s: %s",
                b_name.value,
                op_clean,
                str(exc),
            )
            raise

    def on_success(
        self,
        broker: BrokerName | str,
        operation: str,
        context: BrokerErrorContext | None,
        response: Any,
    ) -> ValidatedResponse | None:
        """Valida a resposta, reseta falhas no circuit breaker e confirma idempotência."""
        if not self.enabled:
            return None

        b_name = BrokerName.from_value(broker)
        op_clean = operation.strip().lower()

        # Validação estrutural rigorosa
        validated = self.validator.validate(b_name, op_clean, response)

        # Atualiza Circuit Breaker
        self.circuit_breaker.record_success(b_name, op_clean)

        # Atualiza idempotência
        if context is not None and context.idempotency_key:
            self.idempotency.mark_confirmed(context.idempotency_key, {"validated": True})

        return validated

    def on_error(
        self,
        context: BrokerErrorContext,
        exc: BaseException | None = None,
    ) -> ErrorDecision:
        """Classifica falha, registra no circuit breaker e emite decisão conservadora."""
        if not self.enabled:
            # Em modo desabilitado, fallback seguro sem afetar comportamento legado
            return self.policy_engine.evaluate(context, ErrorCategory.UNKNOWN)

        # Enriquece o contexto se uma exceção tiver sido passada
        if exc is not None:
            extracted_code = getattr(getattr(exc, "code", None), "value", None)
            context = BrokerErrorContext(
                broker=context.broker,
                operation=context.operation,
                request_id=context.request_id,
                idempotency_key=context.idempotency_key,
                trade_id=context.trade_id,
                http_status=context.http_status,
                broker_code=context.broker_code or extracted_code,
                raw_message=context.raw_message or str(exc),
                exception_type=context.exception_type or type(exc).__name__,
                timestamp_utc=context.timestamp_utc,
                retry_count=context.retry_count,
            )

        decision = self.classifier.classify(context, exc=exc)

        # Registra falha no Circuit Breaker
        self.circuit_breaker.record_failure(
            context.broker,
            context.operation,
            category=decision.category,
        )

        # Se for operação mutante e o resultado for ambíguo, marca como UNKNOWN no tracker
        if (
            context.is_mutating
            and context.idempotency_key
            and decision.category
            in {
                ErrorCategory.TIMEOUT,
                ErrorCategory.NETWORK,
                ErrorCategory.SERVER,
                ErrorCategory.ORDER_UNKNOWN,
            }
        ):
            self.idempotency.mark_unknown(
                context.idempotency_key,
                reason=decision.technical_message,
            )

        return decision

    def can_retry(
        self,
        context: BrokerErrorContext,
        decision: ErrorDecision,
    ) -> bool:
        """Determina se uma nova tentativa é permitida pela política e por regras de negócio."""
        if not self.enabled:
            return False

        # REGRA FUNDAMENTAL: Jamais retentar ordens mutantes após falha
        if context.is_mutating:
            return False

        # Se houver chave de idempotência travada
        if context.idempotency_key:
            can_dispatch, _ = self.idempotency.can_dispatch(context.idempotency_key)
            if not can_dispatch:
                return False

        return decision.retry_allowed and (context.retry_count < decision.max_retries)
