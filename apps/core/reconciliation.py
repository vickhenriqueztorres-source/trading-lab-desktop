from __future__ import annotations

import random
import threading
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

_TRANSIENT_STATUS_QUERY_ERRORS = frozenset(
    {
        ProtocolErrorCode.IPC_FRAME_TRUNCATED,
        ProtocolErrorCode.IPC_CONNECTION_LOST,
        ProtocolErrorCode.IPC_HANDSHAKE_TIMEOUT,
        ProtocolErrorCode.IPC_BACKPRESSURE,
        ProtocolErrorCode.AUTH_IPC_UNAVAILABLE,
        ProtocolErrorCode.AUTH_IPC_REQUEST_TIMEOUT,
        ProtocolErrorCode.LIFECYCLE_IPC_UNAVAILABLE,
        ProtocolErrorCode.LIFECYCLE_IPC_REQUEST_TIMEOUT,
        ProtocolErrorCode.UI_IPC_UNAVAILABLE,
        ProtocolErrorCode.UI_IPC_REQUEST_TIMEOUT,
        ProtocolErrorCode.WORKER_NOT_READY,
        ProtocolErrorCode.WORKER_CRASHED,
        ProtocolErrorCode.RECONCILIATION_UNAVAILABLE,
        ProtocolErrorCode.RECONCILIATION_QUERY_TIMEOUT,
        ProtocolErrorCode.DERIV_DEMO_REAUTH_REQUIRED,
        ProtocolErrorCode.DERIV_BALANCE_UNAVAILABLE,
        ProtocolErrorCode.MD_CLOCK_UNTRUSTED,
    }
)
_DEFINITIVE_STATUS_QUERY_ERRORS = frozenset(ProtocolErrorCode) - _TRANSIENT_STATUS_QUERY_ERRORS


class ReconciliationOutcome(StrEnum):
    RESOLVED = "RESOLVED"
    IDEMPOTENT = "IDEMPOTENT"
    UNRESOLVED = "UNRESOLVED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    FAILED = "FAILED"
    NOT_EXECUTED = "NOT_EXECUTED"


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

    @property
    def transient_count(self) -> int:
        return sum(
            item.outcome in {ReconciliationOutcome.UNRESOLVED, ReconciliationOutcome.FAILED}
            for item in self.results
        )

    @property
    def not_executed_count(self) -> int:
        return sum(item.outcome is ReconciliationOutcome.NOT_EXECUTED for item in self.results)

    @property
    def manual_review_count(self) -> int:
        return sum(
            item.outcome is ReconciliationOutcome.MANUAL_REVIEW_REQUIRED for item in self.results
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

    def unregister(self, broker: Broker | str) -> None:
        key = broker.value if isinstance(broker, Broker) else str(broker).upper()
        self._workers.pop(key, None)

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
        max_query_attempts: int = 4,
        query_timeout: float = 8.0,
        query_timeout_max: float = 20.0,
        retry_delay: float = 1.0,
        retry_backoff_multiplier: float = 2.0,
        retry_delay_max: float = 15.0,
        retry_jitter: float = 0.25,
        not_found_grace_seconds: float = 90.0,
        not_found_confirmation_interval_seconds: float = 10.0,
        sleeper: Callable[[float], None] = time.sleep,
        random_provider: Callable[[], float] = random.random,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            max_query_attempts <= 0
            or query_timeout <= 0
            or query_timeout_max < query_timeout
            or retry_delay < 0
            or retry_backoff_multiplier < 1
            or retry_delay_max < retry_delay
            or not 0 <= retry_jitter <= 1
            or not_found_grace_seconds <= 0
            or not_found_confirmation_interval_seconds <= 0
        ):
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
        self._query_timeout_max = query_timeout_max
        self._retry_delay = retry_delay
        self._retry_backoff_multiplier = retry_backoff_multiplier
        self._retry_delay_max = retry_delay_max
        self._retry_jitter = retry_jitter
        self._not_found_grace_seconds = not_found_grace_seconds
        self._not_found_confirmation_interval_seconds = not_found_confirmation_interval_seconds
        self._sleeper = sleeper
        self._uses_default_sleeper = sleeper is time.sleep
        self._random_provider = random_provider
        self._monotonic = monotonic
        self._cancel_event: threading.Event | None = None

    def set_cancel_event(self, event: threading.Event) -> None:
        self._cancel_event = event

    def reconcile_all(self) -> ReconciliationReport:
        candidates = self._reader.list_reconciliation_candidates()
        results = tuple(self._reconcile(candidate) for candidate in candidates)
        self._clear_gates_after_positive_cycle(bool(candidates), results)
        return ReconciliationReport(results)

    def reconcile_all_brokers(self) -> dict[str, list[ReconciliationOutcome]]:
        candidates = self._reader.list_reconciliation_candidates()
        by_broker: dict[str, list[ReconciliationOutcome]] = {}
        for candidate in candidates:
            broker_name = str(candidate.get("broker", "UNKNOWN"))
            result = self._reconcile(candidate)
            by_broker.setdefault(broker_name, []).append(result.outcome)
        flat_results = tuple(outcome for outcomes in by_broker.values() for outcome in outcomes)
        successful = bool(flat_results) and all(
            outcome
            not in {
                ReconciliationOutcome.FAILED,
                ReconciliationOutcome.UNRESOLVED,
                ReconciliationOutcome.MANUAL_REVIEW_REQUIRED,
            }
            for outcome in flat_results
        )
        self._clear_gates_after_positive_cycle(bool(candidates), (), successful=successful)
        return by_broker

    def reconcile_order(self, order_id: str) -> ReconciliationItemResult:
        """Run the read-only status fallback for one exact Core-owned order."""
        candidate = self._reader.reconciliation_candidate(order_id)
        if candidate is None:
            raise ValueError("order not found")
        result = self._reconcile(candidate)
        self._clear_gates_after_positive_cycle(True, (result,))
        return result

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
                status = self._worker.query_order_status(
                    query,
                    timeout=self._query_timeout_for(query_number),
                )
            except RuntimeError:
                code = ProtocolErrorCode.WORKER_NOT_READY
                self._writer.complete_reconciliation_attempt(
                    attempt_id,
                    "FAILED",
                    code.value,
                )
                if query_number < self._max_query_attempts:
                    self._event_sink.emit(
                        "reconciliation_retry_scheduled",
                        order_id=order_id,
                        attempt=query_number + 1,
                        reason_code=code.value,
                    )
                    self._sleep_before_retry(query_number)
                    continue
                return self._failed(order_id, current_state, code.value)
            except StatusQueryError as exc:
                if self._is_retryable(exc.code) and query_number < self._max_query_attempts:
                    self._writer.complete_reconciliation_attempt(
                        attempt_id,
                        "FAILED",
                        exc.code.value,
                    )
                    self._event_sink.emit(
                        "reconciliation_retry_scheduled",
                        order_id=order_id,
                        attempt=query_number + 1,
                        reason_code=exc.code.value,
                    )
                    self._sleep_before_retry(query_number)
                    continue
                if not self._is_retryable(exc.code):
                    self._writer.complete_reconciliation_attempt(
                        attempt_id,
                        "CONFLICT",
                        exc.code.value,
                    )
                    return self._manual_review(order_id, current_state, exc.code.value)
                self._writer.complete_reconciliation_attempt(
                    attempt_id,
                    "FAILED",
                    exc.code.value,
                )
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
                self._writer.complete_reconciliation_attempt(
                    attempt_id,
                    "UNRESOLVED",
                    reason,
                )
                self._sleep_before_retry(query_number)
                continue
            if status.outcome is StatusQueryOutcome.NOT_FOUND:
                self._event_sink.emit(
                    "reconciliation_not_found",
                    order_id=order_id,
                    reason_code=reason,
                )
                negative = status.not_found_evidence
                if negative is None or not negative.confirms_both_sources:
                    self._writer.complete_reconciliation_attempt(
                        attempt_id,
                        "UNRESOLVED",
                        ProtocolErrorCode.RECONCILIATION_NOT_FOUND.value,
                    )
                    self._health_gate.block("HG_ORDER_UNKNOWN")
                    return ReconciliationItemResult(
                        order_id,
                        ReconciliationOutcome.UNRESOLVED,
                        current_state,
                        ProtocolErrorCode.RECONCILIATION_NOT_FOUND.value,
                    )
                applied = self._writer.apply_reconciliation_not_found(
                    attempt_id,
                    negative,
                    not_found_grace_seconds=self._not_found_grace_seconds,
                    confirmation_interval_seconds=(self._not_found_confirmation_interval_seconds),
                )
                if applied.status is ReconciliationApplyStatus.RESOLVED:
                    self._event_sink.emit(
                        "reconciliation_resolved",
                        order_id=order_id,
                        final_state=applied.order_state.value,
                        reason_code="RECONCILIATION_NOT_FOUND",
                    )
                    return ReconciliationItemResult(
                        order_id,
                        ReconciliationOutcome.NOT_EXECUTED,
                        applied.order_state,
                        "RECONCILIATION_NOT_FOUND",
                    )
                self._health_gate.block("HG_ORDER_UNKNOWN")
                return ReconciliationItemResult(
                    order_id,
                    ReconciliationOutcome.UNRESOLVED,
                    current_state,
                    applied.reason_code,
                )
            if status.outcome is StatusQueryOutcome.INVALID_RESPONSE:
                self._writer.complete_reconciliation_attempt(
                    attempt_id,
                    "CONFLICT",
                    reason,
                )
                return self._manual_review(order_id, current_state, reason)
            self._writer.complete_reconciliation_attempt(attempt_id, "FAILED", reason)
            return self._failed(order_id, current_state, reason)
        raise AssertionError("bounded reconciliation loop did not return")

    def _invalid_result(
        self,
        attempt_id: str,
        order_id: str,
        current_state: OrderState,
    ) -> ReconciliationItemResult:
        reason = ProtocolErrorCode.RECONCILIATION_INVALID_RESPONSE.value
        self._writer.complete_reconciliation_attempt(attempt_id, "CONFLICT", reason)
        return self._manual_review(order_id, current_state, reason)

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

    def _manual_review(
        self,
        order_id: str,
        current_state: OrderState,
        reason: str,
    ) -> ReconciliationItemResult:
        self._health_gate.block("HG_RECONCILIATION_CONFLICT")
        self._event_sink.emit(
            "reconciliation_conflict",
            order_id=order_id,
            reason_code=reason,
        )
        return ReconciliationItemResult(
            order_id,
            ReconciliationOutcome.MANUAL_REVIEW_REQUIRED,
            current_state,
            reason,
        )

    def _clear_gates_after_positive_cycle(
        self,
        attempted: bool,
        results: tuple[ReconciliationItemResult, ...],
        *,
        successful: bool | None = None,
    ) -> None:
        if not attempted:
            return
        if successful is None:
            successful = bool(results) and all(
                item.outcome
                not in {
                    ReconciliationOutcome.FAILED,
                    ReconciliationOutcome.UNRESOLVED,
                    ReconciliationOutcome.MANUAL_REVIEW_REQUIRED,
                }
                for item in results
            )
        pending = self._reader.list_reconciliation_candidates()
        if successful and not pending:
            self._health_gate.clear_if("HG_ORDER_UNKNOWN")
            self._health_gate.clear_if("HG_RECONCILIATION_REQUIRED")
            self._health_gate.clear_if("HG_RECONCILIATION_UNAVAILABLE")
        settlement_unknown = self._reader.list_by_state(
            "orders", OrderState.SETTLEMENT_UNKNOWN.value
        )
        if not settlement_unknown:
            self._health_gate.clear_if("HG_SETTLEMENT_UNKNOWN")

    def _query_timeout_for(self, attempt: int) -> float:
        return min(self._query_timeout_max, self._query_timeout + (attempt - 1) * 4.0)

    def _sleep_before_retry(self, attempt: int) -> None:
        base = min(
            self._retry_delay_max,
            self._retry_delay * self._retry_backoff_multiplier ** (attempt - 1),
        )
        random_value = self._random_provider()
        if not 0 <= random_value <= 1:
            raise ValueError("reconciliation random provider returned an invalid value")
        jitter_factor = 1.0 + ((random_value * 2.0) - 1.0) * self._retry_jitter
        delay = min(self._retry_delay_max, max(0.0, base * jitter_factor))
        deadline = self._monotonic() + delay
        remaining = max(0.0, deadline - self._monotonic())
        if self._cancel_event is None or not self._uses_default_sleeper:
            self._sleeper(remaining)
        else:
            self._cancel_event.wait(remaining)

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
        if code in _TRANSIENT_STATUS_QUERY_ERRORS:
            return True
        if code in _DEFINITIVE_STATUS_QUERY_ERRORS:
            return False
        raise AssertionError(f"unclassified status query error: {code}")

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
