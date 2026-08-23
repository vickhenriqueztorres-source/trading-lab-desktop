from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from apps.deriv_worker.order_session import DerivOrderSession
from apps.deriv_worker.request_allowlist import DerivOperation
from apps.deriv_worker.schema import DerivWorkerError
from apps.deriv_worker.websocket_client import DerivReadTransport
from packages.domain.models import (
    Broker,
    ExternalOrderStatus,
    OrderStatusQuery,
    ReconciliationEvidence,
    ReconciliationSource,
    StatusQueryOutcome,
)
from packages.protocol.messages import OrderStatusResult


class DerivReconciliationHandler:
    """Authoritative reconciliation handler querying the Deriv API for contract status."""

    def __init__(
        self,
        transport: DerivReadTransport,
        order_session: DerivOrderSession,
        *,
        timeout_seconds: float = 3.0,
    ) -> None:
        self._transport = transport
        self._order_session = order_session
        self._timeout_seconds = timeout_seconds

    def query_order_status(
        self,
        query: OrderStatusQuery,
        *,
        causation_id: str | None = None,
    ) -> OrderStatusResult:
        cid = causation_id or str(uuid4())
        if not self._order_session.demo_authenticated:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=cid,
                reason_code="DERIV_REAL_ACCOUNT_FORBIDDEN",
            )
        if query.broker is not Broker.DERIV:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=cid,
                reason_code="DERIV_INVALID_BROKER",
            )

        # 1. Try finding by broker_order_id if present
        contract_data: dict[str, object] | None = None
        raw_bytes: bytes = b""

        if query.broker_order_id:
            try:
                response = self._transport.request(
                    DerivOperation.PROPOSAL_OPEN_CONTRACT,
                    {
                        "proposal_open_contract": 1,
                        "contract_id": int(query.broker_order_id),
                    },
                    timeout=self._timeout_seconds,
                )
                poc = response.get("proposal_open_contract")
                if isinstance(poc, dict) and poc.get("contract_id") is not None:
                    contract_data = poc
                    raw_bytes = json.dumps(poc, sort_keys=True, default=str).encode("utf-8")
            except (DerivWorkerError, OSError, TimeoutError, ValueError):
                pass

        # 2. If not found by contract_id, check tracked orders in order_session
        if contract_data is None:
            tracked = self._order_session.get_tracked_by_order_id(query.order_id)
            if tracked is not None and tracked.contract_id:
                try:
                    response = self._transport.request(
                        DerivOperation.PROPOSAL_OPEN_CONTRACT,
                        {
                            "proposal_open_contract": 1,
                            "contract_id": int(tracked.contract_id),
                        },
                        timeout=self._timeout_seconds,
                    )
                    poc = response.get("proposal_open_contract")
                    if isinstance(poc, dict) and poc.get("contract_id") is not None:
                        contract_data = poc
                        raw_bytes = json.dumps(poc, sort_keys=True, default=str).encode("utf-8")
                except (DerivWorkerError, OSError, TimeoutError, ValueError):
                    pass

        # 3. If still not found, query statement or profit_table
        if contract_data is None:
            try:
                response = self._transport.request(
                    DerivOperation.STATEMENT,
                    {"statement": 1, "description": 1, "limit": 50},
                    timeout=self._timeout_seconds,
                )
                statement = response.get("statement")
                if isinstance(statement, dict):
                    transactions = statement.get("transactions")
                    if isinstance(transactions, list):
                        for tx in transactions:
                            if not isinstance(tx, dict):
                                continue
                            passthrough = tx.get("passthrough")
                            if (
                                isinstance(passthrough, dict)
                                and passthrough.get("order_id") == query.order_id
                            ):
                                contract_id = tx.get("contract_id")
                                if contract_id:
                                    return self._query_contract_id(query, str(contract_id), cid)
            except (DerivWorkerError, OSError, TimeoutError, ValueError):
                pass

        if contract_data is None:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.NOT_FOUND,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=cid,
                reason_code="DERIV_CONTRACT_NOT_FOUND",
            )

        return self._build_evidence_from_contract(query, contract_data, raw_bytes, cid)

    def _query_contract_id(
        self,
        query: OrderStatusQuery,
        contract_id: str,
        causation_id: str,
    ) -> OrderStatusResult:
        try:
            response = self._transport.request(
                DerivOperation.PROPOSAL_OPEN_CONTRACT,
                {
                    "proposal_open_contract": 1,
                    "contract_id": int(contract_id),
                },
                timeout=self._timeout_seconds,
            )
            poc = response.get("proposal_open_contract")
            if isinstance(poc, dict) and poc.get("contract_id") is not None:
                raw_bytes = json.dumps(poc, sort_keys=True, default=str).encode("utf-8")
                return self._build_evidence_from_contract(query, poc, raw_bytes, causation_id)
        except (DerivWorkerError, OSError, TimeoutError, ValueError):
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=causation_id,
                reason_code="DERIV_STATUS_QUERY_UNAVAILABLE",
            )
        return OrderStatusResult(
            outcome=StatusQueryOutcome.NOT_FOUND,
            evidence=None,
            response_message_id=str(uuid4()),
            correlation_id=query.correlation_id,
            causation_id=causation_id,
            reason_code="DERIV_CONTRACT_NOT_FOUND",
        )

    def _build_evidence_from_contract(
        self,
        query: OrderStatusQuery,
        contract_data: Mapping[str, object],
        raw_bytes: bytes,
        causation_id: str,
    ) -> OrderStatusResult:
        contract_id = str(contract_data["contract_id"])
        status = str(contract_data.get("status", "open")).lower()
        is_sold = int(str(contract_data.get("is_sold", 0))) == 1
        is_settled = is_sold or status in ("won", "lost", "sold")

        underlying = contract_data.get("underlying")
        if underlying is not None and str(underlying) != query.symbol:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=causation_id,
                reason_code="DERIV_RECONCILIATION_SYMBOL_MISMATCH",
            )

        contract_type = contract_data.get("contract_type")
        if contract_type is not None and str(contract_type).upper() != query.direction.value:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=causation_id,
                reason_code="DERIV_RECONCILIATION_DIRECTION_MISMATCH",
            )

        currency = contract_data.get("currency")
        if currency is not None and str(currency).upper() != query.amount.currency:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=causation_id,
                reason_code="DERIV_RECONCILIATION_CURRENCY_MISMATCH",
            )

        if is_settled:
            external_status = ExternalOrderStatus.SETTLED
            profit_val = contract_data.get("profit")
            if profit_val is not None:
                profit_decimal = Decimal(str(profit_val))
            else:
                payout_val = Decimal(str(contract_data.get("payout", 0)))
                buy_val = Decimal(
                    str(contract_data.get("buy_price", query.amount.minor_units / 100))
                )
                profit_decimal = payout_val - buy_val
            realized_pnl_minor: int | None = int(profit_decimal * 100)
        else:
            external_status = ExternalOrderStatus.OPEN
            realized_pnl_minor = None

        evidence_id = str(uuid4())
        raw_hash = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else None

        evidence = ReconciliationEvidence(
            evidence_id=evidence_id,
            source=ReconciliationSource.STATUS_QUERY,
            observed_at=datetime.now(UTC),
            client_order_ref=query.client_order_ref,
            broker_order_id=contract_id,
            external_status=external_status,
            broker=Broker.DERIV,
            account_id=query.account_id,
            product=query.product,
            symbol=query.symbol,
            direction=query.direction,
            amount=query.amount,
            evidence_version=1,
            realized_pnl_minor=realized_pnl_minor,
            raw_reference_hash=raw_hash,
        )

        return OrderStatusResult(
            outcome=StatusQueryOutcome.FOUND,
            evidence=evidence,
            response_message_id=str(uuid4()),
            correlation_id=query.correlation_id,
            causation_id=causation_id,
        )
