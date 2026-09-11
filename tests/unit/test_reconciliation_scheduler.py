from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from apps.core.health import HealthGate
from apps.core.reconciliation import ReconciliationReport
from apps.core.reconciliation_scheduler import ReconciliationScheduler


class SchedulerReader:
    def __init__(self, active: bool = False) -> None:
        self.active = active

    def list_reconciliation_candidates(self) -> list[dict[str, object]]:
        if not self.active:
            return []
        return [{"order_id": "order-1", "order_state": "UNKNOWN"}]


class SchedulerCoordinator:
    def __init__(
        self,
        reader: SchedulerReader,
        gate: HealthGate,
        *,
        resolve_after: int = 1,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.reader = reader
        self.gate = gate
        self.resolve_after = resolve_after
        self.entered = entered
        self.release = release
        self.run_count = 0
        self.cancel_event: threading.Event | None = None

    def set_cancel_event(self, event: threading.Event) -> None:
        self.cancel_event = event

    def reconcile_all(self) -> ReconciliationReport:
        self.run_count += 1
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            self.release.wait(1)
        if self.run_count >= self.resolve_after:
            self.reader.active = False
            self.gate.clear_if("HG_ORDER_UNKNOWN")
        return ReconciliationReport(())


def wait_until(predicate: object, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition did not become true")


def test_scheduler_recovers_on_later_cycle_and_then_becomes_idle() -> None:
    reader = SchedulerReader(active=True)
    gate = HealthGate()
    gate.block("HG_ORDER_UNKNOWN")
    coordinator = SchedulerCoordinator(reader, gate, resolve_after=6)
    scheduler = ReconciliationScheduler(
        coordinator,  # type: ignore[arg-type]
        reader,  # type: ignore[arg-type]
        gate,
        reconcile_cycle_seconds=0.005,
        reconcile_cycle_max_seconds=0.02,
    )

    scheduler.start()
    wait_until(lambda: coordinator.run_count == 6)
    idle_count = coordinator.run_count
    time.sleep(0.06)
    scheduler.stop()

    assert idle_count == 6
    assert coordinator.run_count == idle_count
    assert not gate.contains("HG_ORDER_UNKNOWN")


def test_scheduler_does_not_overlap_cycles() -> None:
    reader = SchedulerReader(active=True)
    gate = HealthGate()
    entered = threading.Event()
    release = threading.Event()
    coordinator = SchedulerCoordinator(reader, gate, entered=entered, release=release)
    scheduler = ReconciliationScheduler(
        coordinator,  # type: ignore[arg-type]
        reader,  # type: ignore[arg-type]
        gate,
    )
    first = threading.Thread(target=scheduler.run_once)
    first.start()
    assert entered.wait(1)

    assert scheduler.run_once() is None
    release.set()
    first.join(1)
    scheduler.stop()

    assert coordinator.run_count == 1
    assert not first.is_alive()


def test_scheduler_thread_is_daemon_and_exits_cleanly_when_idle() -> None:
    reader = SchedulerReader()
    gate = HealthGate()
    coordinator = SchedulerCoordinator(reader, gate)
    scheduler = ReconciliationScheduler(
        coordinator,  # type: ignore[arg-type]
        reader,  # type: ignore[arg-type]
        gate,
        reconcile_cycle_seconds=0.01,
        reconcile_cycle_max_seconds=0.02,
    )

    scheduler.start()
    thread = scheduler.thread
    assert thread is not None and thread.daemon
    scheduler.trigger("test")
    wait_until(lambda: coordinator.run_count == 1)
    scheduler.stop()

    assert not thread.is_alive()


def test_scheduler_restores_backoff_from_durable_attempt_history() -> None:
    class DurableReader(SchedulerReader):
        def reconciliation_attempts_for_order(self, _order_id: str):
            return [
                {
                    "result": "UNRESOLVED",
                    "started_at": datetime.now(UTC).isoformat(),
                }
                for _ in range(8)
            ]

    reader = DurableReader(active=True)
    gate = HealthGate()
    gate.block("HG_ORDER_UNKNOWN")
    coordinator = SchedulerCoordinator(reader, gate, resolve_after=99)
    scheduler = ReconciliationScheduler(
        coordinator,  # type: ignore[arg-type]
        reader,  # type: ignore[arg-type]
        gate,
        reconcile_cycle_seconds=5.0,
        reconcile_cycle_max_seconds=900.0,
    )

    scheduler._restore_durable_delay()

    assert scheduler._delay > 899.0


def test_scheduler_prioritizes_second_complete_negative_confirmation() -> None:
    class NegativeProofReader(SchedulerReader):
        def reconciliation_attempts_for_order(self, _order_id: str):
            return [
                {
                    "result": "UNRESOLVED",
                    "reason_code": "RECONCILIATION_NOT_FOUND_BOTH_SOURCES",
                    "started_at": datetime.now(UTC).isoformat(),
                }
            ]

    reader = NegativeProofReader(active=True)
    gate = HealthGate()
    gate.block("HG_ORDER_UNKNOWN")
    coordinator = SchedulerCoordinator(reader, gate, resolve_after=99)
    scheduler = ReconciliationScheduler(
        coordinator,  # type: ignore[arg-type]
        reader,  # type: ignore[arg-type]
        gate,
        reconcile_cycle_seconds=5.0,
        reconcile_cycle_max_seconds=900.0,
    )

    scheduler._restore_durable_delay()

    assert 9.0 < scheduler._delay <= 10.0


def test_unrelated_clock_and_balance_gates_do_not_change_reconciliation_signature() -> None:
    reader = SchedulerReader(active=True)
    gate = HealthGate()
    coordinator = SchedulerCoordinator(reader, gate)
    scheduler = ReconciliationScheduler(
        coordinator,  # type: ignore[arg-type]
        reader,  # type: ignore[arg-type]
        gate,
    )
    before = scheduler._state_signature()
    gate.block("MD_CLOCK_UNTRUSTED")
    gate.block("IQOPTION_BALANCE_STALE")

    assert scheduler._state_signature() == before
