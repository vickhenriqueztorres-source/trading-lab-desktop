"""Modelos de dados e contratos para a camada de Broker Resilience.

Define enums, dataclasses de contexto de erro e decisões padronizadas
para Deriv e IQ Option sem dependências externas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class BrokerName(StrEnum):
    """Identificadores de corretoras suportadas pela resiliência."""

    DERIV = "DERIV"
    IQOPTION = "IQOPTION"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_value(cls, value: Any) -> BrokerName:
        if isinstance(value, cls):
            return value
        if not value:
            return cls.UNKNOWN
        val = str(value).strip().upper()
        if "DERIV" in val:
            return cls.DERIV
        if "IQ" in val:
            return cls.IQOPTION
        return cls.UNKNOWN


class ErrorCategory(StrEnum):
    """Categorias canônicas de falhas em chamadas de broker."""

    RATE_LIMIT = "RATE_LIMIT"
    AUTH = "AUTH"
    MAINTENANCE = "MAINTENANCE"
    NETWORK = "NETWORK"
    TIMEOUT = "TIMEOUT"
    SERVER = "SERVER"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_SYMBOL = "INVALID_SYMBOL"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    ORDER_UNKNOWN = "ORDER_UNKNOWN"
    RESPONSE_SCHEMA = "RESPONSE_SCHEMA"
    UNKNOWN = "UNKNOWN"


class Action(StrEnum):
    """Ações resultantes da política de resolução de erros."""

    RETRY = "RETRY"
    WAIT = "WAIT"
    ABORT = "ABORT"
    RECONCILE = "RECONCILE"
    REAUTHENTICATE = "REAUTHENTICATE"
    OPEN_CIRCUIT = "OPEN_CIRCUIT"
    NOTIFY = "NOTIFY"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class CircuitState(StrEnum):
    """Estados do Circuit Breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


MUTATING_OPERATIONS: frozenset[str] = frozenset(
    {
        "buy",
        "sell",
        "place_order",
        "submit_order",
        "open_contract",
        "order_submit",
    }
)


@dataclass(frozen=True, slots=True)
class BrokerErrorContext:
    """Contexto enriquecido da ocorrência de um erro em operação de broker."""

    broker: BrokerName
    operation: str
    request_id: str | None = None
    idempotency_key: str | None = None
    trade_id: str | None = None
    http_status: int | None = None
    broker_code: str | int | None = None
    raw_message: str | None = None
    exception_type: str | None = None
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(UTC))
    retry_count: int = 0

    @property
    def is_mutating(self) -> bool:
        """Indica se a operação pode ter efeito financeiro ou criar/alterar ordem."""
        return self.operation.lower().strip() in MUTATING_OPERATIONS


@dataclass(frozen=True, slots=True)
class ErrorDecision:
    """Decisão estruturada e conservadora emitida pela camada de resiliência."""

    category: ErrorCategory
    actions: frozenset[Action]
    retry_allowed: bool
    max_retries: int
    retry_after_seconds: float | None
    should_open_circuit: bool
    should_notify_user: bool
    should_reconcile_order: bool
    user_message: str
    technical_message: str


class BrokerResilienceError(Exception):
    """Exceção base para erros da camada de Broker Resilience."""


class BrokerCircuitOpenError(BrokerResilienceError):
    """Levantada quando uma chamada é bloqueada devido a Circuit Breaker aberto."""

    def __init__(
        self,
        broker: BrokerName,
        operation: str,
        remaining_seconds: float | None = None,
        message: str | None = None,
    ) -> None:
        self.broker = broker
        self.operation = operation
        self.remaining_seconds = remaining_seconds
        msg = message or (
            f"Circuit Breaker para {broker.value}:{operation} está ABERTO. "
            f"Aguarde {remaining_seconds:.1f}s para recuperação."
            if remaining_seconds is not None
            else f"Circuit Breaker para {broker.value}:{operation} está ABERTO."
        )
        super().__init__(msg)


class BrokerResponseSchemaError(BrokerResilienceError):
    """Levantada quando a resposta do broker não cumpre o contrato estrutural esperado."""

    def __init__(
        self,
        broker: BrokerName,
        operation: str,
        missing_or_invalid_field: str,
        technical_message: str,
    ) -> None:
        self.broker = broker
        self.operation = operation
        self.field = missing_or_invalid_field
        self.technical_message = technical_message
        super().__init__(
            f"Falha de validação de schema em {broker.value}:{operation} "
            f"(campo '{missing_or_invalid_field}'): {technical_message}"
        )


class BrokerRateLimitExceededError(BrokerResilienceError):
    """Levantada quando uma chamada excede a taxa permitida e o timeout de espera expira."""

    def __init__(
        self,
        broker: BrokerName,
        operation: str,
        retry_after_seconds: float,
    ) -> None:
        self.broker = broker
        self.operation = operation
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Limite de requisições excedido para {broker.value}:{operation}. "
            f"Retry-After: {retry_after_seconds:.2f}s"
        )
