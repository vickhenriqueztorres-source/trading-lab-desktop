"""Testes unitários para o classificador de erros e sanitizador de segredos."""

from __future__ import annotations

from apps.core.broker_resilience.classifier import (
    BrokerErrorClassifier,
    sanitize_sensitive_text,
)
from apps.core.broker_resilience.models import (
    Action,
    BrokerErrorContext,
    BrokerName,
    ErrorCategory,
)


def test_sanitize_sensitive_text_removes_tokens_and_passwords() -> None:
    """Valida que credenciais, tokens, chaves e senhas são estritamente redigidos."""
    raw = (
        "Failed request with token: sample_auth_token_value and password='my_super_secret_pwd!'. "
        "User email: trader.pro@example.com, cookie: ssid_sample_session."
    )
    sanitized = sanitize_sensitive_text(raw)

    assert "sample_auth_token_value" not in sanitized
    assert "my_super_secret_pwd!" not in sanitized
    assert "trader.pro@example.com" not in sanitized
    assert "[REDACTED]" in sanitized
    assert "[REDACTED_EMAIL]" in sanitized


def test_classify_by_structured_broker_code() -> None:
    """Valida que códigos estruturados oficiais do broker têm prioridade máxima."""
    classifier = BrokerErrorClassifier()

    # Deriv Rate Limit
    ctx_rate = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="proposal",
        broker_code="RateLimitExceeded",
    )
    d_rate = classifier.classify(ctx_rate)
    assert d_rate.category is ErrorCategory.RATE_LIMIT

    # IQ Option Auth Failed
    ctx_auth = BrokerErrorContext(
        broker=BrokerName.IQOPTION,
        operation="place_order",
        broker_code="IQOPTION_AUTH_FAILED",
    )
    d_auth = classifier.classify(ctx_auth)
    assert d_auth.category is ErrorCategory.AUTH

    # Deriv Maintenance
    ctx_maint = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="buy",
        broker_code="SystemMaintenance",
    )
    d_maint = classifier.classify(ctx_maint)
    assert d_maint.category is ErrorCategory.MAINTENANCE


def test_classify_by_http_status() -> None:
    """Valida mapeamento quando apenas o código HTTP está disponível."""
    classifier = BrokerErrorClassifier()

    ctx_429 = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="proposal",
        http_status=429,
    )
    assert classifier.classify(ctx_429).category is ErrorCategory.RATE_LIMIT

    ctx_401 = BrokerErrorContext(
        broker=BrokerName.IQOPTION,
        operation="get_balance",
        http_status=401,
    )
    assert classifier.classify(ctx_401).category is ErrorCategory.AUTH

    ctx_500 = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="proposal",
        http_status=500,
    )
    assert classifier.classify(ctx_500).category is ErrorCategory.SERVER


def test_classify_by_textual_fallback() -> None:
    """Valida fallback em expressões textuais explícitas."""
    classifier = BrokerErrorClassifier()

    ctx = BrokerErrorContext(
        broker=BrokerName.IQOPTION,
        operation="market_history",
        raw_message="Connection lost unexpectedly to remote peer",
    )
    assert classifier.classify(ctx).category is ErrorCategory.NETWORK


def test_unclassified_falls_back_to_unknown_manual_review() -> None:
    """Valida que erros sem evidência segura resultam em UNKNOWN + MANUAL_REVIEW."""
    classifier = BrokerErrorClassifier()

    ctx = BrokerErrorContext(
        broker=BrokerName.DERIV,
        operation="custom_test",
        raw_message="something completely bizarre happened",
    )
    decision = classifier.classify(ctx)
    assert decision.category is ErrorCategory.UNKNOWN
    assert Action.MANUAL_REVIEW in decision.actions
    assert decision.retry_allowed is False
