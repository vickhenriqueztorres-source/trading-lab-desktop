"""Classificador de erros e incidentes para Deriv e IQ Option.

Analisa códigos estruturados do broker, HTTP status, tipos de exceção e
mensagens sanitizadas, mapeando-os para decisões determinísticas sem vazar segredos.
"""

from __future__ import annotations

import logging
import re
from typing import Final

from apps.core.broker_resilience.models import (
    BrokerErrorContext,
    ErrorCategory,
    ErrorDecision,
)
from apps.core.broker_resilience.policies import BrokerPolicyEngine
from apps.core.worker_client import DeliveryCertainty
from packages.protocol.errors import ProtocolErrorCode

logger = logging.getLogger("core.broker_resilience.classifier")

# Padrões para sanitização rigorosa de dados sensíveis antes de qualquer log
_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?i)(password|passwd|pwd)[\"':\s=]+([^\s,\";]+)", re.IGNORECASE),
    re.compile(r"(?i)(token|api_token|auth_token|bearer)[\"':\s=]+([^\s,\";]+)", re.IGNORECASE),
    re.compile(r"(?i)(secret|client_secret|device_key)[\"':\s=]+([^\s,\";]+)", re.IGNORECASE),
    re.compile(r"(?i)(ssid|cookie|session_id)[\"':\s=]+([^\s,\";]+)", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
)


def sanitize_sensitive_text(text: str | None) -> str:
    """Substitui tokens, senhas, chaves e e-mails por marcadores seguros."""
    if not text:
        return ""
    sanitized = text
    # Redigir pares de segredo (chave: valor -> chave: [REDACTED])
    sanitized = re.sub(
        r"(?i)\b(password|passwd|pwd|token|api_token|auth_token|bearer|secret|client_secret|device_key|ssid|cookie|session_id)[\"':\s=]+([^\s,\";}]+)",
        r"\1: [REDACTED]",
        sanitized,
    )
    # Redigir e-mails
    sanitized = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        r"[REDACTED_EMAIL]",
        sanitized,
    )
    return sanitized


class BrokerErrorClassifier:
    """Classificador de erros e incidentes independente de transporte."""

    def __init__(self, policy_engine: BrokerPolicyEngine | None = None) -> None:
        self.policy_engine = policy_engine or BrokerPolicyEngine()

    def classify(
        self,
        context: BrokerErrorContext,
        *,
        retry_after: float | None = None,
        exc: BaseException | None = None,
    ) -> ErrorDecision:
        """Classifica o contexto e retorna uma decisão conservadora."""
        category = self._detect_category(context, exc=exc)
        decision = self.policy_engine.evaluate(context, category, retry_after=retry_after)

        # Log estruturado seguro (sem vazar credenciais)
        safe_msg = sanitize_sensitive_text(context.raw_message)
        logger.debug(
            "Classified broker error: broker=%s, op=%s, category=%s, actions=%s, msg=%s",
            context.broker.value,
            context.operation,
            category.value,
            [a.value for a in decision.actions],
            safe_msg[:120],
        )
        return decision

    def _detect_category(
        self,
        context: BrokerErrorContext,
        exc: BaseException | None = None,
    ) -> ErrorCategory:
        """Determina a categoria primária do erro priorizando fontes estruturadas."""
        # 0. Protocolo IPC: certeza de entrega (DeliveryCertainty)
        if exc is not None and getattr(exc, "delivery", None) is DeliveryCertainty.POSSIBLY_SENT:
            return ErrorCategory.ORDER_UNKNOWN

        code_str = str(context.broker_code).strip() if context.broker_code is not None else ""
        exc_str = str(context.exception_type).strip() if context.exception_type is not None else ""
        msg_str = (context.raw_message or "").lower()

        # 1. Códigos estruturados oficiais Deriv / IQ Option / IPC (ProtocolErrorCode)
        if code_str:
            upper_code = code_str.upper()
            # Rate Limit
            if upper_code in {
                "RATELIMIT",
                "RATELIMITEXCEEDED",
                "TOO_MANY_REQUESTS",
                "IPC_BACKPRESSURE",
                ProtocolErrorCode.IPC_BACKPRESSURE.value,
            }:
                return ErrorCategory.RATE_LIMIT

            # Autenticação
            if upper_code in {
                "INVALIDTOKEN",
                "AUTHORIZATIONREQUIRED",
                "DISABLEDUSER",
                "IQOPTION_AUTH_FAILED",
                "NOT_AUTHORIZED",
                "AUTH_FAILED",
                "DERIV_DEMO_REAUTH_REQUIRED",
            }:
                return ErrorCategory.AUTH

            # Manutenção
            if upper_code in {
                "SYSTEMMAINTENANCE",
                "UNDERMAINTENANCE",
                "MAINTENANCE",
                "SERVER_UNDER_MAINTENANCE",
            }:
                return ErrorCategory.MAINTENANCE

            # Símbolo / Mercado Fechado
            if upper_code in {
                "INVALIDSYMBOL",
                "SYMBOLCLOSED",
                "CONTRACTBUYVALIDATIONERROR",
                "IQOPTION_SYMBOL_UNSUPPORTED",
                "IQOPTION_MARKET_CLOSED",
                "IQOPTION_ALL_MARKETS_CLOSED",
                "MARKET_CLOSED",
            }:
                return ErrorCategory.INVALID_SYMBOL

            # Saldo Insuficiente
            if upper_code in {
                "INSUFFICIENTBALANCE",
                "NOBALANCE",
                "INSUFFICIENTFUNDS",
                "NOT_ENOUGH_MONEY",
                "INSUFFICIENT_FUNDS",
            }:
                return ErrorCategory.INSUFFICIENT_FUNDS

            # Timeout / Ordem Ambígua
            if upper_code in {
                "TIMEOUT",
                "REQUESTTIMEOUT",
                "IQOPTION_REQUEST_TIMEOUT",
                "RECONCILIATION_QUERY_TIMEOUT",
                "IPC_HANDSHAKE_TIMEOUT",
                "ORDER_DISPATCH_AMBIGUOUS",
            }:
                return ErrorCategory.TIMEOUT

            # Rede / Conexão Perdida
            if upper_code in {
                "DISCONNECT",
                "CONNECTIONCLOSED",
                "IPC_CONNECTION_LOST",
                "IPC_FRAME_TRUNCATED",
                "WORKER_CRASHED",
                "WORKER_NOT_READY",
                "IQOPTION_WEBSOCKET_UNAVAILABLE",
            }:
                return ErrorCategory.NETWORK

            # Schema / Envelope Inválido
            if upper_code in {
                "IPC_INVALID_ENVELOPE",
                "RESPONSE_SCHEMA_INVALID",
                "SCHEMA_ERROR",
            }:
                return ErrorCategory.RESPONSE_SCHEMA

        # 2. HTTP Status Code (quando disponível)
        if context.http_status is not None:
            status = context.http_status
            if status == 429:
                return ErrorCategory.RATE_LIMIT
            if status in {401, 403}:
                return ErrorCategory.AUTH
            if status == 503:
                return ErrorCategory.MAINTENANCE
            if status == 504:
                return ErrorCategory.TIMEOUT
            if 500 <= status < 600:
                return ErrorCategory.SERVER
            if status in {400, 422}:
                return ErrorCategory.INVALID_REQUEST

        # 3. Tipo de Exceção
        if exc_str:
            exc_lower = exc_str.lower()
            if "timeout" in exc_lower:
                return ErrorCategory.TIMEOUT
            if any(k in exc_lower for k in ("connection", "brokenpipe", "socket", "network")):
                return ErrorCategory.NETWORK
            if "schema" in exc_lower or "validation" in exc_lower:
                return ErrorCategory.RESPONSE_SCHEMA

        # 4. Fallback específico em mensagens textuais sanitizadas
        if "rate limit" in msg_str or "too many requests" in msg_str:
            return ErrorCategory.RATE_LIMIT
        if any(k in msg_str for k in ("unauthorized", "invalid token", "authentication failed")):
            return ErrorCategory.AUTH
        if "maintenance" in msg_str:
            return ErrorCategory.MAINTENANCE
        if any(
            k in msg_str for k in ("insufficient balance", "not enough money", "insufficient funds")
        ):
            return ErrorCategory.INSUFFICIENT_FUNDS
        if any(k in msg_str for k in ("invalid symbol", "market is closed", "symbol unsupported")):
            return ErrorCategory.INVALID_SYMBOL
        if any(k in msg_str for k in ("timed out", "timeout")):
            return ErrorCategory.TIMEOUT
        if any(
            k in msg_str
            for k in ("connection lost", "connection reset", "broken pipe", "connection closed")
        ):
            return ErrorCategory.NETWORK
        if "schema" in msg_str or "missing field" in msg_str:
            return ErrorCategory.RESPONSE_SCHEMA

        # 5. Se não houver evidência segura, retornar UNKNOWN
        return ErrorCategory.UNKNOWN
