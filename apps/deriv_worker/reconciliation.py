from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
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
from packages.protocol.messages import NotFoundEvidence, OrderStatusResult


class DerivLiveReconciliationHandler:
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
        timeout: float | None = None,
    ) -> OrderStatusResult:
        if timeout is not None and timeout <= 0:
            raise ValueError("status query timeout must be positive")
        effective_timeout = timeout if timeout is not None else self._timeout_seconds
        cid = causation_id or str(uuid4())
        if not self._order_session.trading_authenticated:
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
        query_failed = False
        portfolio_checked = False
        statement_checked = False

        if query.broker_order_id:
            try:
                response = self._transport.request(
                    DerivOperation.PROPOSAL_OPEN_CONTRACT,
                    {
                        "proposal_open_contract": 1,
                        "contract_id": int(query.broker_order_id),
                    },
                    timeout=effective_timeout,
                )
                poc = response.get("proposal_open_contract")
                if isinstance(poc, dict) and poc.get("contract_id") is not None:
                    contract_data = poc
                    raw_bytes = json.dumps(poc, sort_keys=True, default=str).encode("utf-8")
            except (DerivWorkerError, OSError, TimeoutError, ValueError):
                query_failed = True

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
                        timeout=effective_timeout,
                    )
                    poc = response.get("proposal_open_contract")
                    if isinstance(poc, dict) and poc.get("contract_id") is not None:
                        contract_data = poc
                        raw_bytes = json.dumps(poc, sort_keys=True, default=str).encode("utf-8")
                except (DerivWorkerError, OSError, TimeoutError, ValueError):
                    query_failed = True

        # 3. Prove absence from the complete set of currently open contracts.
        if contract_data is None:
            try:
                response = self._transport.request(
                    DerivOperation.PORTFOLIO,
                    {"portfolio": 1},
                    timeout=effective_timeout,
                )
                portfolio = response.get("portfolio")
                contracts = portfolio.get("contracts") if isinstance(portfolio, dict) else None
                if not isinstance(contracts, list):
                    query_failed = True
                else:
                    portfolio_checked = True
                    tracked_order = self._order_session.get_tracked_by_order_id(query.order_id)
                    known_contract_ids = {
                        str(value)
                        for value in (
                            query.broker_order_id,
                            (tracked_order.contract_id if tracked_order is not None else None),
                        )
                        if value
                    }
                    for item in contracts:
                        if not isinstance(item, dict):
                            continue
                        contract_id = item.get("contract_id")
                        passthrough = item.get("passthrough")
                        if contract_id is not None and (
                            str(contract_id) in known_contract_ids
                            or (
                                isinstance(passthrough, dict)
                                and passthrough.get("order_id") == query.order_id
                            )
                        ):
                            return self._query_contract_id(
                                query, str(contract_id), cid, timeout=effective_timeout
                            )
            except (DerivWorkerError, OSError, TimeoutError, ValueError):
                query_failed = True

        # 4. If still not found, query statement.
        if contract_data is None:
            try:
                statement_request: dict[str, object] = {
                    "statement": 1,
                    "description": 1,
                    "limit": 999,
                    "action_type": "buy",
                }
                if query.submitted_at is not None:
                    statement_request["date_from"] = int(query.submitted_at.timestamp())
                response = self._transport.request(
                    DerivOperation.STATEMENT,
                    statement_request,
                    timeout=effective_timeout,
                )
                statement = response.get("statement")
                if isinstance(statement, dict):
                    transactions = statement.get("transactions")
                    if isinstance(transactions, list):
                        statement_checked = True
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
                                    return self._query_contract_id(
                                        query, str(contract_id), cid, timeout=effective_timeout
                                    )
                    else:
                        query_failed = True
                else:
                    query_failed = True
            except (DerivWorkerError, OSError, TimeoutError, ValueError):
                query_failed = True

        # 5. Some Deriv account configurations expose the passthrough only in profit_table.
        ambiguous_profit_match = False
        if contract_data is None:
            try:
                response = self._transport.request(
                    DerivOperation.PROFIT_TABLE,
                    {
                        "profit_table": 1,
                        "description": 1,
                        "limit": 500,
                        **(
                            {"date_from": int(query.submitted_at.timestamp())}
                            if query.submitted_at is not None
                            else {}
                        ),
                    },
                    timeout=effective_timeout,
                )
                table = response.get("profit_table")
                transactions = table.get("transactions") if isinstance(table, dict) else None
                if isinstance(transactions, list):
                    for tx in transactions:
                        if not isinstance(tx, dict):
                            continue
                        passthrough = tx.get("passthrough")
                        if not (
                            isinstance(passthrough, dict)
                            and passthrough.get("order_id") == query.order_id
                        ):
                            continue
                        contract_id = tx.get("contract_id")
                        if contract_id is not None:
                            return self._query_contract_id(
                                query, str(contract_id), cid, timeout=effective_timeout
                            )
                    matched_ids = self._match_profit_table_contracts(query, transactions)
                    if len(matched_ids) == 1:
                        return self._query_contract_id(
                            query, matched_ids[0], cid, timeout=effective_timeout
                        )
                    ambiguous_profit_match = len(matched_ids) > 1
            except (DerivWorkerError, OSError, TimeoutError, ValueError):
                query_failed = True

        if contract_data is None:
            if ambiguous_profit_match:
                return OrderStatusResult(
                    outcome=StatusQueryOutcome.INVALID_RESPONSE,
                    evidence=None,
                    response_message_id=str(uuid4()),
                    correlation_id=query.correlation_id,
                    causation_id=cid,
                    reason_code="DERIV_RECONCILIATION_AMBIGUOUS_MATCH",
                )
            negative_evidence = (
                NotFoundEvidence(
                    observed_at=datetime.now(UTC),
                    statement_checked=True,
                    portfolio_checked=True,
                )
                if statement_checked and portfolio_checked and not query_failed
                else None
            )
            return OrderStatusResult(
                outcome=(
                    StatusQueryOutcome.UNAVAILABLE
                    if query_failed or negative_evidence is None
                    else StatusQueryOutcome.NOT_FOUND
                ),
                evidence=None,
                response_message_id=str(uuid4()),
                correlation_id=query.correlation_id,
                causation_id=cid,
                reason_code=(
                    "DERIV_STATUS_QUERY_UNAVAILABLE"
                    if query_failed or negative_evidence is None
                    else "DERIV_CONTRACT_NOT_FOUND"
                ),
                not_found_evidence=negative_evidence,
            )

        return self._build_evidence_from_contract(query, contract_data, raw_bytes, cid)

    @staticmethod
    def _match_profit_table_contracts(
        query: OrderStatusQuery,
        transactions: list[object],
    ) -> tuple[str, ...]:
        """Match an ambiguous buy only from a unique, tightly bounded broker record."""

        if query.broker_order_id is not None or query.submitted_at is None:
            return ()
        expected_price = Decimal(query.amount.minor_units) / Decimal(100)
        expected_product = query.product.strip().upper()
        submitted_epoch = int(query.submitted_at.timestamp())
        matches: set[str] = set()
        for item in transactions:
            if not isinstance(item, dict):
                continue
            contract_id = item.get("contract_id")
            purchase_time = item.get("purchase_time")
            if contract_id is None or isinstance(purchase_time, bool):
                continue
            try:
                observed_epoch = int(str(purchase_time))
                buy_price = Decimal(str(item.get("buy_price")))
            except (ArithmeticError, ValueError):
                continue
            if not submitted_epoch <= observed_epoch <= submitted_epoch + 15:
                continue
            if buy_price != expected_price:
                continue
            if str(item.get("contract_type", "")).strip().upper() != expected_product:
                continue
            if str(item.get("underlying_symbol", "")).strip() != query.symbol:
                continue
            duration_type = item.get("duration_type")
            if duration_type is not None and str(duration_type).strip().lower() != "ticks":
                continue
            matches.add(str(contract_id))
        return tuple(sorted(matches))

    def _query_contract_id(
        self,
        query: OrderStatusQuery,
        contract_id: str,
        causation_id: str,
        *,
        timeout: float | None = None,
    ) -> OrderStatusResult:
        try:
            response = self._transport.request(
                DerivOperation.PROPOSAL_OPEN_CONTRACT,
                {
                    "proposal_open_contract": 1,
                    "contract_id": int(contract_id),
                },
                timeout=timeout if timeout is not None else self._timeout_seconds,
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
        is_expired = int(str(contract_data.get("is_expired", 0))) == 1
        is_settled = is_expired or is_sold or status in ("won", "lost", "sold")

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
        normalized_product = query.product.upper()
        digit_products = {"DIGITDIFF", "DIGITOVER", "DIGITUNDER", "DIGITEVEN", "DIGITODD"}
        expected_contract_type = (
            normalized_product if normalized_product in digit_products else query.direction.value
        )
        if contract_type is not None and str(contract_type).upper() != expected_contract_type:
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

        buy_price = contract_data.get("buy_price")
        if buy_price is not None:
            expected = Decimal(query.amount.minor_units) / Decimal(100)
            try:
                actual = Decimal(str(buy_price))
            except Exception:
                return OrderStatusResult(
                    outcome=StatusQueryOutcome.INVALID_RESPONSE,
                    evidence=None,
                    response_message_id=str(uuid4()),
                    correlation_id=query.correlation_id,
                    causation_id=causation_id,
                    reason_code="DERIV_RECONCILIATION_AMOUNT_INVALID",
                )
            if actual != expected:
                return OrderStatusResult(
                    outcome=StatusQueryOutcome.UNAVAILABLE,
                    evidence=None,
                    response_message_id=str(uuid4()),
                    correlation_id=query.correlation_id,
                    causation_id=causation_id,
                    reason_code="DERIV_RECONCILIATION_AMOUNT_MISMATCH",
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
            realized_pnl_minor: int | None = int(
                (profit_decimal * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
            )
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


# Backward-compatible name retained for the current IPC server imports.
DerivReconciliationHandler = DerivLiveReconciliationHandler
