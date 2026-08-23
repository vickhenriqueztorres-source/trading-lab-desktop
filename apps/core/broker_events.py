from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from apps.core.coordinator import AccountCommandSerializer
from apps.core.health import HealthGate
from apps.core.risk import RiskLedger
from packages.domain.models import BrokerOrderEvent, OrderState
from packages.observability.events import EventSink, NullEventSink
from packages.persistence.reader import StateReader
from packages.persistence.writer import (
    BrokerEventApplyResult,
    BrokerEventApplyStatus,
    SingleDatabaseWriter,
)


class OrderEventSource(Protocol):
    def receive_order_event(self, timeout: float) -> BrokerOrderEvent | None: ...


FallbackReconciliation = Callable[[str], bool]


class BrokerEventProcessor:
    """Validate and commit normalized broker events; owns no transport or submit port."""

    def __init__(
        self,
        writer: SingleDatabaseWriter,
        reader: StateReader,
        health_gate: HealthGate,
        risk_ledger: RiskLedger,
        event_sink: EventSink | None = None,
        *,
        serializer: AccountCommandSerializer | None = None,
        fallback_reconciliation: FallbackReconciliation | None = None,
    ) -> None:
        self._writer = writer
        self._reader = reader
        self._health_gate = health_gate
        self._risk_ledger = risk_ledger
        self._event_sink = event_sink or NullEventSink()
        self._serializer = serializer or AccountCommandSerializer()
        self._fallback_reconciliation = fallback_reconciliation

    def process(self, event: BrokerOrderEvent) -> BrokerEventApplyResult:
        broker = event.broker.value
        account_id = event.account_id
        self._event_sink.emit(
            "broker_event_received",
            event_id=event.event_id,
            correlation_id=event.correlation_id,
            broker=broker,
        )
        with self._serializer.serialize(broker, account_id):
            self._event_sink.emit(
                "broker_event_validated",
                event_id=event.event_id,
                correlation_id=event.correlation_id,
            )
            result = self._writer.apply_normalized_broker_event(event)
            if result.status is BrokerEventApplyStatus.DUPLICATE:
                self._event_sink.emit(
                    "broker_event_duplicate",
                    event_id=event.event_id,
                    correlation_id=event.correlation_id,
                )
            elif result.status is BrokerEventApplyStatus.CONFLICT:
                self._health_gate.block_scope(
                    broker,
                    account_id,
                    "HG_ORDER_EVENT_CONFLICT",
                )
                self._event_sink.emit(
                    "broker_event_replay_conflict",
                    event_id=event.event_id,
                    correlation_id=event.correlation_id,
                    reason_code=result.reason_code,
                )
            else:
                self._event_sink.emit(
                    "broker_event_applied",
                    event_id=event.event_id,
                    correlation_id=event.correlation_id,
                    final_state=(result.order_state.value if result.order_state else None),
                )
                if result.order_state is OrderState.OPEN:
                    self._event_sink.emit(
                        "order_opened",
                        event_id=event.event_id,
                        correlation_id=event.correlation_id,
                    )
                elif result.order_state is OrderState.SETTLED:
                    self._health_gate.clear_scope(
                        broker,
                        account_id,
                        "HG_SETTLEMENT_REQUIRED",
                    )
                    self._event_sink.emit(
                        "order_settled",
                        event_id=event.event_id,
                        correlation_id=event.correlation_id,
                    )
                elif result.order_state is OrderState.SETTLEMENT_UNKNOWN:
                    self._health_gate.block_scope(
                        broker,
                        account_id,
                        "HG_SETTLEMENT_REQUIRED",
                    )
                    self._event_sink.emit(
                        "settlement_unknown",
                        event_id=event.event_id,
                        correlation_id=event.correlation_id,
                    )
            self._risk_ledger.restore(self._reader.list_by_state("risk_reservations", "ACTIVE"))

        if result.status is BrokerEventApplyStatus.APPLIED_WITH_GAP:
            self._health_gate.block_scope(broker, account_id, "HG_ORDER_EVENT_GAP")
            self._event_sink.emit(
                "broker_event_sequence_gap",
                event_id=event.event_id,
                correlation_id=event.correlation_id,
                reason_code=result.reason_code,
            )
            if self._fallback_reconciliation is not None:
                self._event_sink.emit(
                    "event_fallback_reconciliation_started",
                    event_id=event.event_id,
                    correlation_id=event.correlation_id,
                )
                resolved = self._fallback_reconciliation(event.client_order_ref)
                if resolved:
                    self._health_gate.clear_scope(broker, account_id, "HG_ORDER_EVENT_GAP")
                self._event_sink.emit(
                    "event_fallback_reconciliation_completed",
                    event_id=event.event_id,
                    correlation_id=event.correlation_id,
                    resolved=resolved,
                )
        return result


class BrokerEventPump:
    """Drain the client's bounded financial queue without blocking its IPC reader."""

    def __init__(
        self,
        source: OrderEventSource,
        processor: BrokerEventProcessor,
        health_gate: HealthGate,
        event_sink: EventSink | None = None,
    ) -> None:
        self._source = source
        self._processor = processor
        self._health_gate = health_gate
        self._event_sink = event_sink or NullEventSink()
        self._stop = threading.Event()
        self._activity = threading.Condition()
        self._processing = 0
        self._thread = threading.Thread(
            target=self._run,
            name="broker-event-processor",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)

    @property
    def pending_event_count(self) -> int:
        raw_pending = getattr(self._source, "pending_order_event_count", 0)
        queued = raw_pending if type(raw_pending) is int and raw_pending >= 0 else 0
        with self._activity:
            return queued + self._processing

    def drain(self, timeout: float) -> bool:
        """Wait only for already queued/in-flight events; never wait for future settlement."""

        if timeout <= 0:
            raise ValueError("event drain timeout must be positive")
        deadline = time.monotonic() + timeout
        with self._activity:
            while self.pending_event_count > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._activity.wait(timeout=min(remaining, 0.05))
        return True

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                event = self._source.receive_order_event(timeout=0.1)
                if event is not None:
                    with self._activity:
                        self._processing += 1
                    try:
                        self._processor.process(event)
                    finally:
                        with self._activity:
                            self._processing -= 1
                            self._activity.notify_all()
        except Exception:
            if not self._stop.is_set():
                self._health_gate.block("HG_ORDER_EVENT_CONFLICT")
                self._event_sink.emit(
                    "broker_event_processing_failed",
                    reason_code="BROKER_EVENT_PROCESSING_FAILED",
                )
