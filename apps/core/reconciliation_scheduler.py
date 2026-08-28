from __future__ import annotations

import threading
from collections.abc import Callable

from apps.core.health import HealthGate
from apps.core.reconciliation import ReconciliationCoordinator, ReconciliationReport
from packages.observability.events import EventSink, NullEventSink
from packages.persistence.reader import StateReader

_RECONCILIATION_GATES = frozenset(
    {
        "HG_RECONCILIATION_UNAVAILABLE",
        "HG_RECONCILIATION_REQUIRED",
        "HG_ORDER_UNKNOWN",
        "HG_SETTLEMENT_UNKNOWN",
    }
)


class ReconciliationScheduler:
    """Own the blocking reconciliation loop outside all financial hot paths."""

    def __init__(
        self,
        coordinator: ReconciliationCoordinator,
        reader: StateReader,
        health_gate: HealthGate,
        event_sink: EventSink | None = None,
        *,
        reconcile_cycle_seconds: float = 5.0,
        reconcile_cycle_max_seconds: float = 30.0,
        on_cycle_completed: Callable[[ReconciliationReport], None] | None = None,
    ) -> None:
        if reconcile_cycle_seconds <= 0 or reconcile_cycle_max_seconds < reconcile_cycle_seconds:
            raise ValueError("reconciliation scheduler policy is invalid")
        self._coordinator = coordinator
        self._reader = reader
        self._health_gate = health_gate
        self._event_sink = event_sink or NullEventSink()
        self._base_delay = reconcile_cycle_seconds
        self._max_delay = reconcile_cycle_max_seconds
        self._on_cycle_completed = on_cycle_completed
        self._stop = threading.Event()
        self._trigger = threading.Event()
        self._cycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._delay = self._base_delay
        self._last_signature: tuple[object, ...] | None = None
        coordinator.set_cancel_event(self._stop)

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="reconciliation-scheduler",
            daemon=True,
        )
        self._thread.start()

    def trigger(self, reason: str = "external") -> None:
        if self._stop.is_set():
            return
        self._delay = self._base_delay
        self._trigger.set()
        self._event_sink.emit("reconciliation_cycle_requested", reason_code=reason)

    def stop(self) -> None:
        self._stop.set()
        self._trigger.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def run_once(self) -> ReconciliationReport | None:
        if not self._cycle_lock.acquire(blocking=False):
            self._event_sink.emit("reconciliation_cycle_skipped", reason_code="CYCLE_ACTIVE")
            return None
        try:
            if self._stop.is_set():
                self._event_sink.emit("reconciliation_cycle_skipped", reason_code="SHUTDOWN")
                return None
            self._event_sink.emit("reconciliation_cycle_started")
            report = self._coordinator.reconcile_all()
            self._event_sink.emit(
                "reconciliation_cycle_completed",
                resolved=report.resolved_count,
                transient=report.transient_count,
                not_executed=report.not_executed_count,
                manual_review=report.manual_review_count,
            )
            if self._on_cycle_completed is not None:
                self._on_cycle_completed(report)
            return report
        finally:
            self._cycle_lock.release()

    def _run(self) -> None:
        while not self._stop.is_set():
            active_before_wait = self._is_active()
            if active_before_wait:
                triggered = self._trigger.wait(self._delay)
            else:
                # Discover newly durable ambiguity without running reconciliation
                # cycles while idle. Transport recovery still wakes this immediately.
                triggered = self._trigger.wait(self._base_delay)
            self._trigger.clear()
            if self._stop.is_set():
                return
            if not triggered and not active_before_wait and not self._is_active():
                continue
            before = self._state_signature()
            try:
                report = self.run_once()
            except Exception:
                self._health_gate.block("HG_RECONCILIATION_UNAVAILABLE")
                self._event_sink.emit(
                    "reconciliation_cycle_skipped",
                    reason_code="CYCLE_FAILED",
                )
                self._delay = self._max_delay
                continue
            after = self._state_signature()
            if report is None:
                continue
            active = bool(after[0]) or bool(set(after[1]) & _RECONCILIATION_GATES)
            if not active:
                self._event_sink.emit("reconciliation_cycle_skipped", reason_code="IDLE")
                self._delay = self._max_delay
                continue
            if after != before or after != self._last_signature:
                self._delay = self._base_delay
            else:
                self._delay = min(self._max_delay, self._delay * 2.0)
            self._last_signature = after

    def _is_active(self) -> bool:
        signature = self._state_signature()
        return bool(signature[0]) or bool(set(signature[1]) & _RECONCILIATION_GATES)

    def _state_signature(self) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
        candidates = self._reader.list_reconciliation_candidates()
        candidate_signature = tuple(
            (str(item["order_id"]), str(item["order_state"])) for item in candidates
        )
        blockers = self._health_gate.get_snapshot().active_blockers
        return candidate_signature, blockers
