from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from apps.core.health import HealthGate
from packages.domain.models import utc_now
from packages.observability.events import EventSink, NullEventSink
from packages.persistence.reader import StateReader
from packages.persistence.writer import SingleDatabaseWriter


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    safe_pending_message_ids: tuple[str, ...]
    expired_message_ids: tuple[str, ...]
    ambiguous_message_ids: tuple[str, ...]
    nonterminal_order_ids: tuple[str, ...]
    active_reservation_ids: tuple[str, ...]


class RecoveryCoordinator:
    def __init__(
        self,
        writer: SingleDatabaseWriter,
        reader: StateReader,
        health_gate: HealthGate,
        event_sink: EventSink | None = None,
    ) -> None:
        self._writer = writer
        self._reader = reader
        self._health_gate = health_gate
        self._event_sink = event_sink or NullEventSink()

    def recover(self, now: datetime | None = None) -> RecoveryReport:
        recovered_at = now or utc_now()
        self._event_sink.emit("recovery_started")
        interrupted_count = self._writer.recover_interrupted_dispatches(recovered_at)
        expired = self._writer.cancel_expired_pending_messages(recovered_at)
        pending = self._reader.list_by_state("outbox_messages", "PENDING")
        ambiguous = self._reader.list_by_state("outbox_messages", "AMBIGUOUS")
        nonterminal = self._reader.list_nonterminal_orders()
        active = self._reader.list_by_state("risk_reservations", "ACTIVE")
        safe_pending = tuple(
            row
            for row in pending
            if (order := self._reader.order_for_intent(str(row["intent_id"]))) is not None
            and order["state"] == "OUTBOXED"
        )
        safe_intents = {str(row["intent_id"]) for row in safe_pending}
        unsafe_nonterminal = tuple(
            row
            for row in nonterminal
            if not (row["state"] == "OUTBOXED" and str(row["intent_id"]) in safe_intents)
        )
        if ambiguous:
            self._health_gate.block("HG_ORDER_UNKNOWN")
            self._event_sink.emit(
                "outbox_ambiguous_detected",
                reason_code="HG_ORDER_UNKNOWN",
                count=len(ambiguous),
            )
        elif unsafe_nonterminal:
            self._health_gate.block("HG_RECONCILIATION_REQUIRED")
        report = RecoveryReport(
            safe_pending_message_ids=tuple(str(row["message_id"]) for row in safe_pending),
            expired_message_ids=expired,
            ambiguous_message_ids=tuple(row["message_id"] for row in ambiguous),
            nonterminal_order_ids=tuple(row["order_id"] for row in unsafe_nonterminal),
            active_reservation_ids=tuple(row["reservation_id"] for row in active),
        )
        self._event_sink.emit(
            "recovery_completed",
            interrupted_dispatches=interrupted_count,
            safe_pending=len(report.safe_pending_message_ids),
            ambiguous=len(report.ambiguous_message_ids),
            expired=len(report.expired_message_ids),
        )
        return report
