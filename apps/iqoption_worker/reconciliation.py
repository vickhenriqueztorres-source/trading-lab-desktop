from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from apps.iqoption_worker.order_session import IQOptionOrderSession
from apps.iqoption_worker.schema import IQOptionWorkerError
from packages.brokers.iqoption.community_read_only import IQOptionExternalError
from packages.domain.models import (
    Broker,
    ExternalOrderStatus,
    OrderStatusQuery,
    ReconciliationEvidence,
    ReconciliationSource,
    StatusQueryOutcome,
)
from packages.protocol.messages import OrderStatusResult


class IQOptionReconciliationTransport(Protocol):
    def request(
        self,
        name: str,
        msg: Mapping[str, Any],
        *,
        timeout: float = 2.0,
    ) -> dict[str, Any]: ...


class IQOptionReconciliationHandler:
    """Handles authoritative order status queries and reconciliation for IQ Option."""

    def __init__(
        self,
        transport: IQOptionReconciliationTransport,
        order_session: IQOptionOrderSession,
        *,
        timeout_seconds: float = 2.0,
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
        cid = causation_id or query.correlation_id

        if query.broker is not Broker.IQ_OPTION:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=cid,
                reason_code="IQOPTION_BROKER_MISMATCH",
            )

        contract_data: dict[str, Any] | None = None
        raw_bytes: bytes = b""

        # 1. Query the exact broker id through the authoritative binary-option
        # status route. Recent option history is only a compatibility fallback.
        if query.broker_order_id is not None and query.broker_order_id.strip():
            try:
                response = self._transport.request(
                    "get_betinfo",
                    {"id": int(query.broker_order_id)},
                    timeout=self._timeout_seconds,
                )
                if response.get("isSuccessful"):
                    res = response.get("result")
                    if (
                        isinstance(res, dict)
                        and str(res.get("id", res.get("option_id"))) == query.broker_order_id
                    ):
                        contract_data = res
                        raw_bytes = json.dumps(res, sort_keys=True, default=str).encode("utf-8")
            except (IQOptionWorkerError, IQOptionExternalError, OSError, TimeoutError, ValueError):
                pass

        if contract_data is None and query.broker_order_id is not None:
            try:
                response = self._transport.request(
                    "get_options",
                    {"id": int(query.broker_order_id)},
                    timeout=self._timeout_seconds,
                )
                if response.get("isSuccessful"):
                    res = response.get("result")
                    if (
                        isinstance(res, dict)
                        and str(res.get("id", res.get("option_id"))) == query.broker_order_id
                    ):
                        contract_data = res
                        raw_bytes = json.dumps(res, sort_keys=True, default=str).encode("utf-8")
            except (IQOptionWorkerError, IQOptionExternalError, OSError, TimeoutError, ValueError):
                pass

        # 2. Query by the durable client reference when the submit response was
        # ambiguous and therefore did not yield a broker id. The transport must
        # return an exact client_order_id match; otherwise reconciliation remains
        # fail-closed as NOT_FOUND/UNKNOWN.
        if contract_data is None:
            try:
                response = self._transport.request(
                    "get_options",
                    {"client_order_id": query.order_id},
                    timeout=self._timeout_seconds,
                )
                if response.get("isSuccessful"):
                    res = response.get("result")
                    if (
                        isinstance(res, dict)
                        and res.get("client_order_id") == query.client_order_ref
                    ):
                        contract_data = res
                        raw_bytes = json.dumps(res, sort_keys=True, default=str).encode("utf-8")
            except (IQOptionWorkerError, IQOptionExternalError, OSError, TimeoutError, ValueError):
                pass

        if contract_data is None:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.NOT_FOUND,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=cid,
                reason_code="IQOPTION_OPTION_NOT_FOUND",
            )

        return self._build_evidence_from_contract(query, contract_data, raw_bytes, cid)

    def _build_evidence_from_contract(
        self,
        query: OrderStatusQuery,
        contract: Mapping[str, Any],
        raw_bytes: bytes,
        causation_id: str,
    ) -> OrderStatusResult:
        contract_symbol = str(contract.get("active", contract.get("symbol", "")))
        contract_direction = str(contract.get("direction", "")).upper()
        contract_currency = str(contract.get("currency", "")).upper()

        if contract_symbol and contract_symbol != query.symbol:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=causation_id,
                reason_code="IQOPTION_SYMBOL_MISMATCH",
            )

        if contract_direction and contract_direction != query.direction.value:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=causation_id,
                reason_code="IQOPTION_DIRECTION_MISMATCH",
            )

        if contract_currency and contract_currency != query.amount.currency:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=causation_id,
                reason_code="IQOPTION_CURRENCY_MISMATCH",
            )

        status_str = str(contract.get("status", contract.get("result", ""))).lower()
        win_str = str(contract.get("win", "")).lower()

        if status_str == "open" or win_str == "equal":
            external_status = ExternalOrderStatus.OPEN
            realized_pnl_minor = None
        elif status_str in ("win", "loose") or win_str in ("win", "loose"):
            external_status = ExternalOrderStatus.SETTLED
            win_amount_str = str(contract.get("profit_amount", contract.get("win_amount", "0.00")))
            win_decimal = Decimal(win_amount_str)
            stake_decimal = Decimal(query.amount.minor_units) / Decimal(100)
            pnl_decimal = win_decimal - stake_decimal
            realized_pnl_minor = int(pnl_decimal * Decimal(100))
        else:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=causation_id,
                reason_code="IQOPTION_UNKNOWN_CONTRACT_STATUS",
            )

        contract_id_val = contract.get("id", contract.get("contract_id"))
        broker_order_id = str(contract_id_val) if contract_id_val is not None else None
        raw_hash = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else None

        evidence = ReconciliationEvidence(
            evidence_id=str(uuid4()),
            source=ReconciliationSource.STATUS_QUERY,
            observed_at=datetime.now(UTC),
            client_order_ref=query.client_order_ref,
            broker_order_id=broker_order_id,
            external_status=external_status,
            broker=Broker.IQ_OPTION,
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
            reason_code=None,
        )
