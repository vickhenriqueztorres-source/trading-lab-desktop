"""Validador estrito de respostas e contratos de payload para brokers.

Garante que nenhum acesso a response['campo'] seja feito sem verificação prévia de
estrutura, tipos e integridade, levantando BrokerResponseSchemaError em vez de KeyError.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from apps.core.broker_resilience.classifier import sanitize_sensitive_text
from apps.core.broker_resilience.models import (
    BrokerName,
    BrokerResponseSchemaError,
)

logger = logging.getLogger("core.broker_resilience.validator")


@dataclass(frozen=True, slots=True)
class ValidatedResponse:
    """Resultado imutável de validação de resposta da API do broker."""

    broker: BrokerName
    operation: str
    data: dict[str, Any]
    sanitized_summary: str


class BrokerResponseValidator:
    """Validador de esquemas de resposta específico para Deriv e IQ Option."""

    def validate(
        self,
        broker: BrokerName | str,
        operation: str,
        response: Any,
    ) -> ValidatedResponse:
        """Valida a estrutura do payload retornado pelo broker ou socket."""
        b_name = BrokerName.from_value(broker)
        op_clean = str(operation).strip().lower()

        # 1. Validação de container básico
        if hasattr(response, "__dataclass_fields__"):
            data = {f: getattr(response, f) for f in response.__dataclass_fields__}
        elif isinstance(response, Mapping):
            data = dict(response)
        elif hasattr(response, "__dict__"):
            data = dict(response.__dict__)
        else:
            raise BrokerResponseSchemaError(
                broker=b_name,
                operation=op_clean,
                missing_or_invalid_field="root",
                technical_message=(
                    f"Payload raiz deve ser um mapeamento/dicionário, "
                    f"recebido: {type(response).__name__}"
                ),
            )

        # 2. Amostra segura para logging
        try:
            sample_str = json.dumps({k: data[k] for k in list(data.keys())[:10]}, default=str)
        except Exception:
            sample_str = str(data)[:200]
        sanitized_summary = sanitize_sensitive_text(sample_str)

        # 3. Verificação de erros explícitos embutidos no payload da corretora
        if "error" in data and isinstance(data["error"], Mapping):
            err_dict = data["error"]
            err_msg = str(err_dict.get("message", "Broker returned error envelope"))
            raise BrokerResponseSchemaError(
                broker=b_name,
                operation=op_clean,
                missing_or_invalid_field="error",
                technical_message=f"Broker reported error in envelope: {err_msg}",
            )

        # 4. Validadores especializados por corretora e operação
        if b_name is BrokerName.DERIV:
            self._validate_deriv(op_clean, data, b_name)
        elif b_name is BrokerName.IQOPTION:
            self._validate_iqoption(op_clean, data, b_name)

        logger.debug(
            "Validated response for %s:%s (summary: %s)",
            b_name.value,
            op_clean,
            sanitized_summary[:100],
        )
        return ValidatedResponse(
            broker=b_name,
            operation=op_clean,
            data=data,
            sanitized_summary=sanitized_summary,
        )

    def _validate_deriv(self, operation: str, data: dict[str, Any], broker: BrokerName) -> None:
        """Valida regras de contrato da Deriv."""
        if operation in {"proposal", "quote_digit_contract"}:
            # Deve conter objeto proposal ou campos diretos de ask_price e payout
            proposal = data.get("proposal")
            if isinstance(proposal, Mapping):
                for req in ("id", "ask_price", "payout"):
                    if req not in proposal:
                        raise BrokerResponseSchemaError(
                            broker=broker,
                            operation=operation,
                            missing_or_invalid_field=f"proposal.{req}",
                            technical_message=(
                                f"Campo obrigatório 'proposal.{req}' ausente na cotação."
                            ),
                        )
            else:
                for req in ("proposal_id", "ask_price", "payout"):
                    if req not in data:
                        raise BrokerResponseSchemaError(
                            broker=broker,
                            operation=operation,
                            missing_or_invalid_field=req,
                            technical_message=(
                                f"Campo obrigatório '{req}' ausente na cotação Deriv."
                            ),
                        )

        elif operation in {"buy", "place_order", "submit_order"}:
            buy_payload = data.get("buy")
            if isinstance(buy_payload, Mapping):
                if "contract_id" not in buy_payload:
                    raise BrokerResponseSchemaError(
                        broker=broker,
                        operation=operation,
                        missing_or_invalid_field="buy.contract_id",
                        technical_message=(
                            "Campo 'buy.contract_id' ausente na resposta de compra Deriv."
                        ),
                    )
            elif (
                "contract_id" not in data
                and "order_id" not in data
                and "transaction_id" not in data
                and "broker_order_id" not in data
                and "outcome" not in data
            ):
                raise BrokerResponseSchemaError(
                    broker=broker,
                    operation=operation,
                    missing_or_invalid_field="contract_id",
                    technical_message=(
                        "Identificador de contrato/ordem ausente na confirmação de compra."
                    ),
                )

    def _validate_iqoption(self, operation: str, data: dict[str, Any], broker: BrokerName) -> None:
        """Valida regras de contrato da IQ Option."""
        if operation in {"market_history", "candles"}:
            candles = data.get("candles")
            if not isinstance(candles, list):
                raise BrokerResponseSchemaError(
                    broker=broker,
                    operation=operation,
                    missing_or_invalid_field="candles",
                    technical_message="Campo 'candles' ausente ou não é uma lista de velas.",
                )

        elif operation in {"place_order", "buy", "order_submit", "submit_order"}:
            if (
                "order_id" not in data
                and "id" not in data
                and "status" not in data
                and "broker_order_id" not in data
                and "outcome" not in data
            ):
                raise BrokerResponseSchemaError(
                    broker=broker,
                    operation=operation,
                    missing_or_invalid_field="order_id",
                    technical_message=(
                        "Identificador 'order_id' ou 'id' ausente na ordem IQ Option."
                    ),
                )
