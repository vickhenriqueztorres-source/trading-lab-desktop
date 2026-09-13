"""Políticas determinísticas de decisão e tratamento de erros para brokers.

Define regras conservadoras para operações de leitura vs. operações mutantes,
garantindo que ordens financeiras nunca sejam retentadas às cegas após timeout
ou desconexão.
"""

from __future__ import annotations

from typing import Final

from apps.core.broker_resilience.models import (
    Action,
    BrokerErrorContext,
    ErrorCategory,
    ErrorDecision,
)

DEFAULT_MAX_READ_RETRIES: Final[int] = 3
DEFAULT_RETRY_BASE_DELAY_SECONDS: Final[float] = 1.0
DEFAULT_RETRY_MAX_DELAY_SECONDS: Final[float] = 10.0


def calculate_backoff_delay(
    retry_count: int,
    *,
    base_delay: float = DEFAULT_RETRY_BASE_DELAY_SECONDS,
    max_delay: float = DEFAULT_RETRY_MAX_DELAY_SECONDS,
    retry_after: float | None = None,
) -> float:
    """Calcula o tempo de espera antes da próxima tentativa com teto máximo."""
    if retry_after is not None and retry_after > 0:
        return min(float(retry_after), max_delay)
    delay = base_delay * (2.0 ** max(0, retry_count))
    return min(delay, max_delay)


class BrokerPolicyEngine:
    """Avaliador central de decisões de política para falhas de broker."""

    def __init__(
        self,
        *,
        max_read_retries: int = DEFAULT_MAX_READ_RETRIES,
        retry_base_delay: float = DEFAULT_RETRY_BASE_DELAY_SECONDS,
        retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY_SECONDS,
    ) -> None:
        self.max_read_retries = max(0, max_read_retries)
        self.retry_base_delay = max(0.1, retry_base_delay)
        self.retry_max_delay = max(self.retry_base_delay, retry_max_delay)

    def evaluate(
        self,
        context: BrokerErrorContext,
        category: ErrorCategory,
        *,
        retry_after: float | None = None,
    ) -> ErrorDecision:
        """Gera uma decisão imutável e conservadora baseada na categoria e contexto."""
        # REGRA CRÍTICA: Mutações (buy, place_order, submit_order) diante de timeout,
        # queda de conexão ou erro de servidor NUNCA devem sofrer retentativa cega.
        if context.is_mutating and category in {
            ErrorCategory.TIMEOUT,
            ErrorCategory.NETWORK,
            ErrorCategory.SERVER,
            ErrorCategory.ORDER_UNKNOWN,
        }:
            return ErrorDecision(
                category=ErrorCategory.ORDER_UNKNOWN,
                actions=frozenset({Action.ABORT, Action.RECONCILE, Action.NOTIFY}),
                retry_allowed=False,
                max_retries=0,
                retry_after_seconds=None,
                should_open_circuit=False,
                should_notify_user=True,
                should_reconcile_order=True,
                user_message=(
                    f"Ordem em {context.broker.value} teve resposta indeterminada. "
                    "Reconciliação em andamento para evitar duplicidade."
                ),
                technical_message=(
                    f"Mutating operation '{context.operation}' timed out or lost connection "
                    f"(category={category.value}). Forced ORDER_UNKNOWN + RECONCILE."
                ),
            )

        if category is ErrorCategory.RATE_LIMIT:
            can_retry = context.retry_count < self.max_read_retries
            delay = calculate_backoff_delay(
                context.retry_count,
                base_delay=self.retry_base_delay,
                max_delay=self.retry_max_delay,
                retry_after=retry_after,
            )
            actions = (
                frozenset({Action.WAIT, Action.RETRY})
                if can_retry
                else frozenset({Action.WAIT, Action.ABORT, Action.NOTIFY})
            )
            return ErrorDecision(
                category=category,
                actions=actions,
                retry_allowed=can_retry,
                max_retries=self.max_read_retries,
                retry_after_seconds=delay,
                should_open_circuit=False,
                should_notify_user=not can_retry,
                should_reconcile_order=False,
                user_message=(
                    f"Limite de requisições na {context.broker.value}. Aguardando {delay:.1f}s."
                ),
                technical_message=(
                    f"Rate limited on '{context.operation}'. retry_after={delay:.2f}s"
                ),
            )

        if category is ErrorCategory.AUTH:
            return ErrorDecision(
                category=category,
                actions=frozenset({Action.ABORT, Action.REAUTHENTICATE, Action.NOTIFY}),
                retry_allowed=False,
                max_retries=0,
                retry_after_seconds=None,
                should_open_circuit=False,
                should_notify_user=True,
                should_reconcile_order=False,
                user_message=(
                    f"Falha de autenticação na {context.broker.value}. Reautenticação necessária."
                ),
                technical_message=(
                    f"Authentication failed (status={context.http_status}, "
                    f"code={context.broker_code})"
                ),
            )

        if category is ErrorCategory.MAINTENANCE:
            return ErrorDecision(
                category=category,
                actions=frozenset({Action.ABORT, Action.OPEN_CIRCUIT, Action.NOTIFY}),
                retry_allowed=False,
                max_retries=0,
                retry_after_seconds=None,
                should_open_circuit=True,
                should_notify_user=True,
                should_reconcile_order=False,
                user_message=(
                    f"{context.broker.value} está em manutenção. "
                    "Operações pausadas temporariamente."
                ),
                technical_message=(
                    f"Broker maintenance reported on operation '{context.operation}'."
                ),
            )

        if category in {ErrorCategory.TIMEOUT, ErrorCategory.NETWORK}:
            can_retry = context.retry_count < self.max_read_retries
            delay = calculate_backoff_delay(
                context.retry_count,
                base_delay=self.retry_base_delay,
                max_delay=self.retry_max_delay,
            )
            if can_retry:
                actions = frozenset({Action.RETRY, Action.WAIT})
                should_open = False
            else:
                actions = frozenset({Action.ABORT, Action.OPEN_CIRCUIT, Action.NOTIFY})
                should_open = True

            return ErrorDecision(
                category=category,
                actions=actions,
                retry_allowed=can_retry,
                max_retries=self.max_read_retries,
                retry_after_seconds=delay if can_retry else None,
                should_open_circuit=should_open,
                should_notify_user=not can_retry,
                should_reconcile_order=False,
                user_message=(
                    f"Instabilidade temporária de rede com {context.broker.value}. "
                    + (
                        "Tentando novamente..."
                        if can_retry
                        else "Operação abortada após tentativas."
                    )
                ),
                technical_message=(
                    f"Read/query operation '{context.operation}' experienced {category.value} "
                    f"(attempt {context.retry_count + 1}/{self.max_read_retries + 1})."
                ),
            )

        if category is ErrorCategory.SERVER:
            can_retry = (not context.is_mutating) and (context.retry_count < self.max_read_retries)
            delay = calculate_backoff_delay(
                context.retry_count,
                base_delay=self.retry_base_delay,
                max_delay=self.retry_max_delay,
            )
            actions = (
                frozenset({Action.RETRY, Action.WAIT})
                if can_retry
                else frozenset({Action.ABORT, Action.OPEN_CIRCUIT, Action.NOTIFY})
            )
            return ErrorDecision(
                category=category,
                actions=actions,
                retry_allowed=can_retry,
                max_retries=self.max_read_retries if not context.is_mutating else 0,
                retry_after_seconds=delay if can_retry else None,
                should_open_circuit=not can_retry,
                should_notify_user=not can_retry,
                should_reconcile_order=False,
                user_message=f"Servidor da {context.broker.value} retornou erro interno.",
                technical_message=f"HTTP 5xx / Server error on '{context.operation}'.",
            )

        if category is ErrorCategory.RESPONSE_SCHEMA:
            return ErrorDecision(
                category=category,
                actions=frozenset({Action.ABORT, Action.NOTIFY}),
                retry_allowed=False,
                max_retries=0,
                retry_after_seconds=None,
                should_open_circuit=False,
                should_notify_user=True,
                should_reconcile_order=False,
                user_message=f"Resposta inesperada recebida de {context.broker.value}.",
                technical_message=(
                    f"Response schema validation failed on '{context.operation}'. Fail-closed."
                ),
            )

        if category is ErrorCategory.INVALID_SYMBOL:
            return ErrorDecision(
                category=category,
                actions=frozenset({Action.ABORT, Action.NOTIFY}),
                retry_allowed=False,
                max_retries=0,
                retry_after_seconds=None,
                should_open_circuit=False,  # Não abre circuit para erro de símbolo!
                should_notify_user=True,
                should_reconcile_order=False,
                user_message=f"Símbolo inválido ou indisponível na {context.broker.value}.",
                technical_message=(
                    f"Invalid symbol rejected by {context.broker.value} on '{context.operation}'."
                ),
            )

        if category is ErrorCategory.INSUFFICIENT_FUNDS:
            return ErrorDecision(
                category=category,
                actions=frozenset({Action.ABORT, Action.NOTIFY}),
                retry_allowed=False,
                max_retries=0,
                retry_after_seconds=None,
                should_open_circuit=False,  # Não abre circuit para saldo insuficiente!
                should_notify_user=True,
                should_reconcile_order=False,
                user_message=(
                    f"Saldo insuficiente na conta {context.broker.value} para realizar a operação."
                ),
                technical_message=f"Insufficient funds on '{context.operation}'.",
            )

        if category is ErrorCategory.INVALID_REQUEST:
            return ErrorDecision(
                category=category,
                actions=frozenset({Action.ABORT, Action.NOTIFY}),
                retry_allowed=False,
                max_retries=0,
                retry_after_seconds=None,
                should_open_circuit=False,
                should_notify_user=True,
                should_reconcile_order=False,
                user_message="Parâmetros de requisição inválidos.",
                technical_message=(
                    f"Invalid request rejected by {context.broker.value} on '{context.operation}'."
                ),
            )

        # Fallback conservador: UNKNOWN
        return ErrorDecision(
            category=ErrorCategory.UNKNOWN,
            actions=frozenset({Action.ABORT, Action.MANUAL_REVIEW, Action.NOTIFY}),
            retry_allowed=False,
            max_retries=0,
            retry_after_seconds=None,
            should_open_circuit=False,
            should_notify_user=True,
            should_reconcile_order=context.is_mutating,
            user_message=(
                f"Ocorreu um erro inesperado na {context.broker.value}. Revisão manual recomendada."
            ),
            technical_message=(
                f"Unclassified error on '{context.operation}' "
                f"(http_status={context.http_status}, broker_code={context.broker_code}, "
                f"exception={context.exception_type}). Conservative action applied."
            ),
        )
