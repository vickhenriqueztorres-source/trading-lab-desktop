from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from apps.iqoption_worker.order_session import IQOptionOrderSession
from apps.iqoption_worker.schema import IQOptionWorkerError
from packages.brokers.iqoption.community_read_only import IQOptionExternalError
from packages.brokers.iqoption.result_parser import (
    IQOptionResultError,
    IQOptionResultSource,
    parse_iqoption_financial_result,
)
from packages.domain.models import (
    Broker,
    OrderStatusQuery,
    ReconciliationEvidence,
    ReconciliationSource,
    StatusQueryOutcome,
)
from packages.protocol.messages import NotFoundEvidence, OrderStatusResult


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
        result_source: IQOptionResultSource | None = None
        raw_bytes: bytes = b""
        valid_empty_response = False
        last_unavailable_reason: str | None = None
        not_found_evidence: NotFoundEvidence | None = None

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
                        result_source = IQOptionResultSource.BETINFO
                        raw_bytes = json.dumps(res, sort_keys=True, default=str).encode("utf-8")
                else:
                    valid_empty_response = True
            except (
                IQOptionWorkerError,
                IQOptionExternalError,
                OSError,
                TimeoutError,
                ValueError,
            ) as exc:
                last_unavailable_reason = getattr(
                    exc,
                    "reason_code",
                    "IQOPTION_RECONCILIATION_UNAVAILABLE",
                )

        if contract_data is None and query.broker_order_id is not None:
            try:
                response = self._transport.request(
                    "get_options",
                    self._history_query_payload(query, broker_order_id=query.broker_order_id),
                    timeout=self._timeout_seconds,
                )
                if response.get("isSuccessful"):
                    res = response.get("result")
                    if (
                        isinstance(res, dict)
                        and str(res.get("id", res.get("option_id"))) == query.broker_order_id
                    ):
                        contract_data = res
                        result_source = IQOptionResultSource.OPTIONS_HISTORY
                        raw_bytes = json.dumps(res, sort_keys=True, default=str).encode("utf-8")
                else:
                    coverage = self._negative_evidence_from_response(response)
                    not_found_evidence = coverage or not_found_evidence
                    reason_code = response.get("reason_code")
                    if isinstance(reason_code, str) and reason_code:
                        last_unavailable_reason = reason_code
                    else:
                        valid_empty_response = True
            except (
                IQOptionWorkerError,
                IQOptionExternalError,
                OSError,
                TimeoutError,
                ValueError,
            ) as exc:
                last_unavailable_reason = getattr(
                    exc,
                    "reason_code",
                    "IQOPTION_RECONCILIATION_UNAVAILABLE",
                )

        # 2. Query by the durable client reference when the submit response was
        # ambiguous and therefore did not yield a broker id. The transport may
        # recover that reference only from one complete historical fingerprint;
        # otherwise reconciliation remains fail-closed as NOT_FOUND/UNKNOWN.
        if contract_data is None:
            try:
                response = self._transport.request(
                    "get_options",
                    self._history_query_payload(query),
                    timeout=self._timeout_seconds,
                )
                if response.get("isSuccessful"):
                    res = response.get("result")
                    if (
                        isinstance(res, dict)
                        and res.get("client_order_id") == query.client_order_ref
                    ):
                        contract_data = res
                        result_source = IQOptionResultSource.OPTIONS_HISTORY
                        raw_bytes = json.dumps(res, sort_keys=True, default=str).encode("utf-8")
                else:
                    coverage = self._negative_evidence_from_response(response)
                    not_found_evidence = coverage or not_found_evidence
                    reason_code = response.get("reason_code")
                    if isinstance(reason_code, str) and reason_code:
                        last_unavailable_reason = reason_code
                    else:
                        valid_empty_response = True
            except (
                IQOptionWorkerError,
                IQOptionExternalError,
                OSError,
                TimeoutError,
                ValueError,
            ) as exc:
                last_unavailable_reason = getattr(
                    exc,
                    "reason_code",
                    "IQOPTION_RECONCILIATION_UNAVAILABLE",
                )

        if contract_data is None:
            # Any failed route makes coverage incomplete even when another
            # source returned a valid empty result.  "Not found" is reserved
            # for a wholly successful search, and still carries no proof of
            # non-execution unless the protocol supplies explicit coverage.
            if last_unavailable_reason is not None or not valid_empty_response:
                return OrderStatusResult(
                    outcome=StatusQueryOutcome.UNAVAILABLE,
                    evidence=None,
                    response_message_id=str(uuid4()),
                    correlation_id=query.correlation_id,
                    causation_id=cid,
                    reason_code=last_unavailable_reason or "IQOPTION_RECONCILIATION_UNAVAILABLE",
                )
            return OrderStatusResult(
                outcome=StatusQueryOutcome.NOT_FOUND,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=cid,
                reason_code="IQOPTION_OPTION_NOT_FOUND",
                not_found_evidence=not_found_evidence,
            )

        if result_source is None:  # defensive: data and its source are one atomic observation
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=cid,
                reason_code="IQOPTION_RESULT_SOURCE_INVALID",
            )

        return self._build_evidence_from_contract(
            query,
            contract_data,
            raw_bytes,
            cid,
            result_source,
        )

    @staticmethod
    def _history_query_payload(
        query: OrderStatusQuery,
        *,
        broker_order_id: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "client_order_id": query.client_order_ref,
            "symbol": query.symbol,
            "direction": query.direction.value,
            "amount_minor": query.amount.minor_units,
            "currency": query.amount.currency,
            "submitted_at": (
                None if query.submitted_at is None else query.submitted_at.isoformat()
            ),
        }
        if broker_order_id is not None:
            payload["id"] = int(broker_order_id)
        return payload

    @staticmethod
    def _negative_evidence_from_response(
        response: Mapping[str, Any],
    ) -> NotFoundEvidence | None:
        coverage = response.get("not_found_coverage")
        if not isinstance(coverage, Mapping):
            return None
        try:
            return NotFoundEvidence.from_payload(coverage)
        except ValueError:
            return None

    def _build_evidence_from_contract(
        self,
        query: OrderStatusQuery,
        contract: Mapping[str, Any],
        raw_bytes: bytes,
        causation_id: str,
        result_source: IQOptionResultSource,
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

        observed_at = datetime.now(UTC)
        try:
            financial_result = parse_iqoption_financial_result(
                contract,
                result_source,
                expected_stake_minor=query.amount.minor_units,
                observed_at=observed_at,
            )
        except IQOptionResultError as exc:
            return OrderStatusResult(
                outcome=StatusQueryOutcome.UNAVAILABLE,
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=causation_id,
                reason_code=exc.reason_code,
            )

        contract_id_val = contract.get("id", contract.get("contract_id"))
        broker_order_id = str(contract_id_val) if contract_id_val is not None else None
        raw_hash = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else None

        evidence = ReconciliationEvidence(
            evidence_id=str(uuid4()),
            source=ReconciliationSource.STATUS_QUERY,
            observed_at=observed_at,
            client_order_ref=query.client_order_ref,
            broker_order_id=broker_order_id,
            external_status=financial_result.external_status,
            broker=Broker.IQ_OPTION,
            account_id=query.account_id,
            product=query.product,
            symbol=query.symbol,
            direction=query.direction,
            amount=query.amount,
            # v2 identifies the source-specific parser that proves finality
            # and money fields instead of defaulting absent values to zero.
            evidence_version=2,
            realized_pnl_minor=financial_result.realized_pnl_minor,
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
