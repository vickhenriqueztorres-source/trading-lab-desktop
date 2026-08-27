from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from apps.core.health import HealthGate
from apps.core.worker_client import OrderStatusPort, StatusQueryError
from packages.domain.models import (
    Broker,
    Direction,
    Money,
    OrderState,
    OrderStatusQuery,
    StatusQueryOutcome,
)
from packages.observability.events import EventSink, NullEventSink
from packages.persistence.reader import StateReader
from packages.persistence.writer import (
    ReconciliationApplyStatus,
    SingleDatabaseWriter,
)
from packages.protocol.errors import ProtocolErrorCode
from packages.protocol.messages import OrderStatusResult


class ReconciliationOutcome(StrEnum):
    RESOLVED = "RESOLVED"
    IDEMPOTENT = "IDEMPOTENT"
    UNRESOLVED = "UNRESOLVED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ReconciliationItemResult:
    order_id: str
    outcome: ReconciliationOutcome
    order_state: OrderState
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    results: tuple[ReconciliationItemResult, ...]

    @property
    def resolved_count(self) -> int:
        return sum(
            item.outcome in {ReconciliationOutcome.RESOLVED, ReconciliationOutcome.IDEMPOTENT}
            for item in self.results
        )


class MultiBrokerStatusRouter(OrderStatusPort):
    """Routes status queries to the appropriate broker worker port."""

    def __init__(
        self,
        workers: Mapping[Broker | str, OrderStatusPort] | None = None,
    ) -> None:
        self._workers: dict[str, OrderStatusPort] = {}
        if workers is not None:
            for b, w in workers.items():
                self.register(b, w)

    def register(self, broker: Broker | str, worker: OrderStatusPort) -> None:
        key = broker.value if isinstance(broker, Broker) else str(broker).upper()
        self._workers[key] = worker

    def query_order_status(
        self, query: OrderStatusQuery, *, timeout: float | None = None
    ) -> OrderStatusResult:
        key = query.broker.value if isinstance(query.broker, Broker) else str(query.broker).upper()
        worker = self._workers.get(key)
        if worker is None:
            raise StatusQueryError(
                ProtocolErrorCode.WORKER_NOT_READY,
                f"no status worker registered for broker {key}",
            )
        return worker.query_order_status(query, timeout=timeout)


class ReconciliationCoordinator:
    """Resolve Core ambiguity only through a worker's read-only status boundary."""

    def __init__(
        self,
        writer: SingleDatabaseWriter,
        reader: StateReader,
        worker: OrderStatusPort | Mapping[Broker | str, OrderStatusPort],
        health_gate: HealthGate,
        event_sink: EventSink | None = None,
        *,
        max_query_attempts: int = 2,
        query_timeout: float = 0.5,
        retry_delay: float = 0.05,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_query_attempts <= 0 or query_timeout <= 0 or retry_delay < 0:
            raise ValueError("reconciliation retry policy is invalid")
        self._writer = writer
        self._reader = reader
        if isinstance(worker, Mapping):
            self._worker: OrderStatusPort = MultiBrokerStatusRouter(worker)
        else:
            self._worker = worker
        self._health_gate = health_gate
        self._event_sink = event_sink or NullEventSink()
        self._max_query_attempts = max_query_attempts
        self._query_timeout = query_timeout
        self._retry_delay = retry_delay
        self._sleeper = sleeper

    def reconcile_all(self) -> ReconciliationReport:
        candidates = self._reader.list_reconciliation_candidates()
        results = tuple(self._reconcile(candidate) for candidate in candidates)
        if all(
            item.outcome in {ReconciliationOutcome.RESOLVED, ReconciliationOutcome.IDEMPOTENT}
            for item in results
        ):
            self._health_gate.clear_if("HG_ORDER_UNKNOWN")
            self._health_gate.clear_if("HG_RECONCILIATION_REQUIRED")
            self._health_gate.clear_if("HG_RECONCILIATION_UNAVAILABLE")
        return ReconciliationReport(results)

    def reconcile_all_brokers(self) -> dict[str, list[ReconciliationOutcome]]:
        candidates = self._reader.list_reconciliation_candidates()
        by_broker: dict[str, list[ReconciliationOutcome]] = {}
        for candidate in candidates:
            broker_name = str(candidate.get("broker", "UNKNOWN"))
            result = self._reconcile(candidate)
            by_broker.setdefault(broker_name, []).append(result.outcome)
        if all(
            outcome in {ReconciliationOutcome.RESOLVED, ReconciliationOutcome.IDEMPOTENT}
            for outcomes in by_broker.values()
            for outcome in outcomes
        ):
            self._health_gate.clear_if("HG_ORDER_UNKNOWN")
            self._health_gate.clear_if("HG_RECONCILIATION_REQUIRED")
            self._health_gate.clear_if("HG_RECONCILIATION_UNAVAILABLE")
        return by_broker

    def reconcile_order(self, order_id: str) -> ReconciliationItemResult:
        """Run the read-only status fallback for one exact Core-owned order."""
        candidate = self._reader.reconciliation_candidate(order_id)
        if candidate is None:
            raise ValueError("order not found")
        return self._reconcile(candidate)

    def _reconcile(self, candidate: dict[str, object]) -> ReconciliationItemResult:
        order_id = str(candidate["order_id"])
        current_state = OrderState(str(candidate["order_state"]))
        query = self._query_from_candidate(candidate)
        self._event_sink.emit(
            "reconciliation_started",
            order_id=order_id,
            correlation_id=query.correlation_id,
        )
        for query_number in range(1, self._max_query_attempts + 1):
            attempt_id = str(uuid4())
            self._writer.begin_reconciliation_attempt(
                attempt_id,
                order_id,
                query.correlation_id,
            )
            try:
                status = self._worker.query_order_status(query, timeout=self._query_timeout)
            except StatusQueryError as exc:
                self._writer.complete_reconciliation_attempt(
                    attempt_id,
                    "FAILED",
                    exc.code.value,
                )
                if self._is_retryable(exc.code) and query_number < self._max_query_attempts:
                    self._event_sink.emit(
                        "reconciliation_retry_scheduled",
                        order_id=order_id,
                        attempt=query_number + 1,
                        reason_code=exc.code.value,
                    )
                    self._sleeper(self._retry_delay)
                    continue
                return self._failed(order_id, current_state, exc.code.value)

            if status.outcome is StatusQueryOutcome.FOUND:
                if status.evidence is None:
                    return self._invalid_result(attempt_id, order_id, current_state)
                applied = self._writer.apply_reconciliation_evidence(
                    attempt_id,
                    status.evidence,
                )
                if applied.status is ReconciliationApplyStatus.CONFLICT:
                    self._health_gate.block("HG_RECONCILIATION_CONFLICT")
                    self._event_sink.emit(
                        "reconciliation_conflict",
                        order_id=order_id,
                        reason_code=applied.reason_code,
                    )
                    return ReconciliationItemResult(
                        order_id,
                        ReconciliationOutcome.MANUAL_REVIEW_REQUIRED,
                        applied.order_state,
                        applied.reason_code,
                    )
                if applied.status is ReconciliationApplyStatus.UNRESOLVED:
                    self._health_gate.block("HG_ORDER_UNKNOWN")
                    self._event_sink.emit(
                        "reconciliation_failed",
                        order_id=order_id,
                        reason_code=applied.reason_code,
                    )
                    return ReconciliationItemResult(
                        order_id,
                        ReconciliationOutcome.UNRESOLVED,
                        applied.order_state,
                        applied.reason_code,
                    )
                outcome = (
                    ReconciliationOutcome.IDEMPOTENT
                    if applied.status is ReconciliationApplyStatus.IDEMPOTENT
                    else ReconciliationOutcome.RESOLVED
                )
                if applied.order_state is OrderState.SETTLEMENT_UNKNOWN:
                    self._health_gate.block("HG_SETTLEMENT_UNKNOWN")
                self._event_sink.emit(
                    "reconciliation_resolved",
                    order_id=order_id,
                    final_state=applied.order_state.value,
                )
                return ReconciliationItemResult(
                    order_id,
                    outcome,
                    applied.order_state,
                    None,
                )

            reason = status.reason_code or self._reason_for_outcome(status.outcome)
            self._writer.complete_reconciliation_attempt(
                attempt_id,
                "UNRESOLVED",
                reason,
            )
            if (
                status.outcome
                in {
                    StatusQueryOutcome.UNAVAILABLE,
                    StatusQueryOutcome.QUERY_TIMEOUT,
                }
                and query_number < self._max_query_attempts
            ):
                self._event_sink.emit(
                    "reconciliation_retry_scheduled",
                    order_id=order_id,
                    attempt=query_number + 1,
                    reason_code=reason,
                )
                self._sleeper(self._retry_delay)
                continue
            if status.outcome is StatusQueryOutcome.NOT_FOUND:
                self._event_sink.emit(
                    "reconciliation_not_found",
                    order_id=order_id,
                    reason_code=reason,
                )
                self._health_gate.block("HG_ORDER_UNKNOWN")
                return ReconciliationItemResult(
                    order_id,
                    ReconciliationOutcome.UNRESOLVED,
                    current_state,
                    reason,
                )
            return self._failed(order_id, current_state, reason)
        raise AssertionError("bounded reconciliation loop did not return")

    def _invalid_result(
        self,
        attempt_id: str,
        order_id: str,
        current_state: OrderState,
    ) -> ReconciliationItemResult:
        reason = ProtocolErrorCode.RECONCILIATION_INVALID_RESPONSE.value
        self._writer.complete_reconciliation_attempt(attempt_id, "FAILED", reason)
        return self._failed(order_id, current_state, reason)

    def _failed(
        self,
        order_id: str,
        current_state: OrderState,
        reason: str,
    ) -> ReconciliationItemResult:
        self._health_gate.block("HG_RECONCILIATION_UNAVAILABLE")
        self._event_sink.emit(
            "reconciliation_failed",
            order_id=order_id,
            reason_code=reason,
        )
        return ReconciliationItemResult(
            order_id,
            ReconciliationOutcome.FAILED,
            current_state,
            reason,
        )

    @staticmethod
    def _query_from_candidate(candidate: dict[str, object]) -> OrderStatusQuery:
        return OrderStatusQuery(
            correlation_id=str(candidate["correlation_id"]),
            intent_id=str(candidate["intent_id"]),
            order_id=str(candidate["order_id"]),
            client_order_ref=str(candidate["order_id"]),
            broker=Broker(str(candidate["broker"])),
            account_id=str(candidate["account_id"]),
            product=str(candidate["product"]),
            symbol=str(candidate["symbol"]),
            direction=Direction(str(candidate["direction"])),
            amount=Money(int(str(candidate["amount_minor"])), str(candidate["currency"])),
            broker_order_id=(
                str(candidate["broker_order_id"])
                if candidate["broker_order_id"] is not None
                else None
            ),
            submitted_at=datetime.fromisoformat(str(candidate["order_created_at"])),
        )

    @staticmethod
    def _is_retryable(code: ProtocolErrorCode) -> bool:
        return code in {
            ProtocolErrorCode.RECONCILIATION_QUERY_TIMEOUT,
            ProtocolErrorCode.RECONCILIATION_UNAVAILABLE,
        }

    @staticmethod
    def _reason_for_outcome(outcome: StatusQueryOutcome) -> str:
        return {
            StatusQueryOutcome.NOT_FOUND: ProtocolErrorCode.RECONCILIATION_NOT_FOUND.value,
            StatusQueryOutcome.UNAVAILABLE: ProtocolErrorCode.RECONCILIATION_UNAVAILABLE.value,
            StatusQueryOutcome.QUERY_TIMEOUT: (
                ProtocolErrorCode.RECONCILIATION_QUERY_TIMEOUT.value
            ),
            StatusQueryOutcome.INVALID_RESPONSE: (
                ProtocolErrorCode.RECONCILIATION_INVALID_RESPONSE.value
            ),
            StatusQueryOutcome.FOUND: ProtocolErrorCode.RECONCILIATION_INVALID_RESPONSE.value,
        }[outcome]
