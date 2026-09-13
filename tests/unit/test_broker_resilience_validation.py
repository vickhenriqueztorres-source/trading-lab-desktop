"""Testes unitários para o validador de respostas de broker."""

from __future__ import annotations

import pytest

from apps.core.broker_resilience.models import (
    BrokerName,
    BrokerResponseSchemaError,
)
from apps.core.broker_resilience.response_validator import (
    BrokerResponseValidator,
    ValidatedResponse,
)


def test_validator_rejects_non_mapping_payload() -> None:
    """Valida rejeição segura quando o payload não for dicionário/mapeamento."""
    validator = BrokerResponseValidator()
    with pytest.raises(BrokerResponseSchemaError) as exc_info:
        validator.validate(BrokerName.DERIV, "proposal", "not a dict")
    assert "Payload raiz deve ser um mapeamento" in str(exc_info.value)


def test_validator_rejects_error_envelope() -> None:
    """Valida que erros explícitos no payload da API são detectados."""
    validator = BrokerResponseValidator()
    payload = {
        "error": {
            "code": "InvalidContract",
            "message": "Contract parameters are not valid for this symbol",
        }
    }
    with pytest.raises(BrokerResponseSchemaError) as exc_info:
        validator.validate(BrokerName.DERIV, "proposal", payload)
    assert "Contract parameters are not valid" in str(exc_info.value)


def test_deriv_proposal_missing_field_raises_schema_error_without_keyerror() -> None:
    """Valida que campo ausente na Deriv dispara schema error sem KeyError solto."""
    validator = BrokerResponseValidator()
    # Falta 'payout'
    incomplete = {
        "proposal": {
            "id": "prop-12345",
            "ask_price": 10.5,
        }
    }
    with pytest.raises(BrokerResponseSchemaError) as exc_info:
        validator.validate(BrokerName.DERIV, "proposal", incomplete)
    assert "proposal.payout" in str(exc_info.value)


def test_deriv_proposal_valid_payload_succeeds() -> None:
    """Valida aceitação de cotação válida da Deriv."""
    validator = BrokerResponseValidator()
    valid = {
        "proposal": {
            "id": "prop-9999",
            "ask_price": 5.0,
            "payout": 9.5,
        }
    }
    result = validator.validate(BrokerName.DERIV, "proposal", valid)
    assert isinstance(result, ValidatedResponse)
    assert result.broker is BrokerName.DERIV
    assert result.data["proposal"]["id"] == "prop-9999"


def test_iqoption_candles_validation() -> None:
    """Valida estrutura de histórico de velas da IQ Option."""
    validator = BrokerResponseValidator()

    # Inválido: sem lista de candles
    with pytest.raises(BrokerResponseSchemaError):
        validator.validate(BrokerName.IQOPTION, "market_history", {"status": "ok"})

    # Válido
    valid = {
        "candles": [
            {"from": 1000, "open": 1.1, "close": 1.2},
            {"from": 1060, "open": 1.2, "close": 1.15},
        ]
    }
    res = validator.validate(BrokerName.IQOPTION, "market_history", valid)
    assert len(res.data["candles"]) == 2
