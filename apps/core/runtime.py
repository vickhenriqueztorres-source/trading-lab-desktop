from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from apps.core.broker_events import BrokerEventProcessor, BrokerEventPump, OrderEventSource
from apps.core.coordinator import EntryAuthorizationPort, OrderCoordinator, PersistedOrder
from apps.core.health import HealthGate
from apps.core.instance import CoreInstanceGuard, CoreInstanceGuardError
from apps.core.reconciliation import (
    ReconciliationCoordinator,
    ReconciliationOutcome,
    ReconciliationReport,
)
from apps.core.recovery import RecoveryCoordinator, RecoveryReport
from apps.core.risk import RiskLedger
from apps.core.worker_client import WorkerPort
from apps.core.worker_supervisor import WorkerSupervisor
from apps.simulated_worker.scenarios import WorkerScenario
from packages.domain.models import OrderCommand, OrderRequest
from packages.observability.events import EventSink, NullEventSink
from packages.persistence.backup import DatabaseBackupService
from packages.persistence.health import DatabaseFailureReason, DatabaseHealth
from packages.persistence.reader import StateReader
from packages.persistence.writer import SingleDatabaseWriter


class CoreRuntime:
    """Own the single-Core startup, recovery, dispatch eligibility and shutdown order."""

    def __init__(
        self,
        profile_directory: Path,
        worker: WorkerPort | None = None,
        event_sink: EventSink | None = None,
        *,
        worker_scenario: WorkerScenario = WorkerScenario.ACCEPT,
        simulated_broker_store_path: Path | None = None,
        worker_event_queue_size: int = 128,
        entry_authorizer_factory: Callable[[HealthGate], EntryAuthorizationPort] | None = None,
    ) -> None:
        self.profile_directory = profile_directory
        self.database_path = profile_directory / "state.db"
        self.event_sink = event_sink or NullEventSink()
        self.database_health = DatabaseHealth()
        self.health_gate = HealthGate(self.database_health)
        self.instance_guard = CoreInstanceGuard(profile_directory, self.event_sink)
        self.worker = worker
        self.worker_scenario = worker_scenario
        self.simulated_broker_store_path = (
            simulated_broker_store_path or profile_directory / "simulated_broker_state.db"
        )
        self.worker_supervisor: WorkerSupervisor | None = None
        self.worker_event_queue_size = worker_event_queue_size
        self.entry_authorizer = (
            None if entry_authorizer_factory is None else entry_authorizer_factory(self.health_gate)
        )
        self.dispatcher_started = False
        self._writer: SingleDatabaseWriter | None = None
        self._reader: StateReader | None = None
        self._coordinator: OrderCoordinator | None = None
        self._backup_service: DatabaseBackupService | None = None
        self._recovery_report: RecoveryReport | None = None
        self._reconciliation_coordinator: ReconciliationCoordinator | None = None
        self._reconciliation_report: ReconciliationReport | None = None
        self._risk_ledger: RiskLedger | None = None
        self._broker_event_processor: BrokerEventProcessor | None = None
        self._broker_event_pump: BrokerEventPump | None = None

    @property
    def writer(self) -> SingleDatabaseWriter:
        if self._writer is None:
            raise RuntimeError("Core runtime is not started")
        return self._writer

    @property
    def reader(self) -> StateReader:
        if self._reader is None:
            raise RuntimeError("Core runtime is not started")
        return self._reader

    @property
    def coordinator(self) -> OrderCoordinator:
        if self._coordinator is None:
            raise RuntimeError("Core runtime is not started")
        return self._coordinator

    @property
    def risk_ledger(self) -> RiskLedger:
        if self._risk_ledger is None:
            raise RuntimeError("Core runtime is not started")
        return self._risk_ledger

    @property
    def backup_service(self) -> DatabaseBackupService:
        if self._backup_service is None:
            raise RuntimeError("Core runtime is not started")
        return self._backup_service

    @property
    def recovery_report(self) -> RecoveryReport:
        if self._recovery_report is None:
            raise RuntimeError("Core runtime is not started")
        return self._recovery_report

    @property
    def reconciliation_report(self) -> ReconciliationReport:
        if self._reconciliation_report is None:
            raise RuntimeError("Core runtime has not run reconciliation")
        return self._reconciliation_report

    def start(self) -> RecoveryReport:
        if self._writer is not None:
            return self.recovery_report
        try:
            self.instance_guard.acquire()
        except CoreInstanceGuardError:
            self.database_health.mark_failed(DatabaseFailureReason.DB_LOCK_FAILED)
            raise
        try:
            writer = SingleDatabaseWriter(
                self.database_path,
                database_health=self.database_health,
                event_sink=self.event_sink,
            )
            self._writer = writer
            reader = StateReader(self.database_path)
            self._reader = reader
            recovery = RecoveryCoordinator(
                writer,
                reader,
                self.health_gate,
                self.event_sink,
            )
            report = recovery.recover()
            worker = self.worker
            if worker is None:
                supervisor = WorkerSupervisor(
                    self.health_gate,
                    scenario=self.worker_scenario,
                    event_sink=self.event_sink,
                    broker_store_path=self.simulated_broker_store_path,
                    event_queue_size=self.worker_event_queue_size,
                )
                worker = supervisor.start()
                self.worker_supervisor = supervisor
                self.worker = worker
            reconciliation = ReconciliationCoordinator(
                writer,
                reader,
                worker,
                self.health_gate,
                self.event_sink,
            )
            self._reconciliation_coordinator = reconciliation
            if reader.list_reconciliation_candidates():
                self._reconciliation_report = reconciliation.reconcile_all()
            else:
                self._reconciliation_report = ReconciliationReport(())
            risk_ledger = RiskLedger()
            risk_ledger.restore(reader.list_by_state("risk_reservations", "ACTIVE"))
            self._risk_ledger = risk_ledger
            coordinator = OrderCoordinator(
                writer,
                worker,
                self.health_gate,
                risk_ledger=risk_ledger,
                entry_authorizer=self.entry_authorizer,
            )
            self._coordinator = coordinator
            if hasattr(worker, "receive_order_event"):

                def fallback(order_id: str) -> bool:
                    item = reconciliation.reconcile_order(order_id)
                    return item.outcome in {
                        ReconciliationOutcome.RESOLVED,
                        ReconciliationOutcome.IDEMPOTENT,
                    }

                processor = BrokerEventProcessor(
                    writer,
                    reader,
                    self.health_gate,
                    risk_ledger,
                    self.event_sink,
                    fallback_reconciliation=fallback,
                )
                pump = BrokerEventPump(
                    cast(OrderEventSource, worker),
                    processor,
                    self.health_gate,
                    self.event_sink,
                )
                self._broker_event_processor = processor
                self._broker_event_pump = pump
                pump.start()
            self._backup_service = DatabaseBackupService(writer, self.event_sink)
            self._recovery_report = report
            self.dispatcher_started = self.health_gate.state.is_open
            return report
        except Exception:
            self.dispatcher_started = False
            try:
                if self.worker_supervisor is not None:
                    self.worker_supervisor.shutdown()
                    self.worker_supervisor = None
                if self._writer is not None:
                    self._writer.close()
                    self._writer = None
            finally:
                self._reader = None
                self._coordinator = None
                self._risk_ledger = None
                self._backup_service = None
                self._reconciliation_coordinator = None
                self._reconciliation_report = None
                self._broker_event_processor = None
                self._broker_event_pump = None
                self.worker = None
                self.instance_guard.release()
            raise

    def submit(self, request: OrderRequest, *, dispatch: bool = True) -> PersistedOrder:
        return self.coordinator.submit(request, dispatch=dispatch)

    def dispatch_pending(self) -> OrderCommand | None:
        if not self.dispatcher_started:
            raise RuntimeError("dispatcher is not started")
        return self.coordinator.dispatch_pending()

    def stop_new_entries(self) -> None:
        self.dispatcher_started = False
        self.health_gate.block("HG_SAFE_STOP")

    @property
    def safe_stop_active(self) -> bool:
        return self.health_gate.contains("HG_SAFE_STOP")

    def resume_new_entries(self) -> bool:
        """Clear only the operator safe stop; every other blocker remains authoritative."""

        if self._writer is None:
            raise RuntimeError("Core runtime is not started")
        self.health_gate.clear_if("HG_SAFE_STOP")
        self.dispatcher_started = self.health_gate.state.is_open
        return self.dispatcher_started

    def drain_financial_events(self, timeout: float) -> bool:
        """Drain events already accepted by IPC without waiting for future broker outcomes."""

        if timeout <= 0:
            raise ValueError("drain timeout must be positive")
        pump = self._broker_event_pump
        return True if pump is None else pump.drain(timeout)

    @property
    def pending_financial_event_count(self) -> int:
        pump = self._broker_event_pump
        return 0 if pump is None else pump.pending_event_count

    def shutdown_workers(self, grace_seconds: float = 1.0) -> bool:
        """Stop the simulated worker while the event pump can still persist queued events."""

        if grace_seconds <= 0:
            raise ValueError("worker shutdown grace must be positive")
        if self.worker_supervisor is not None:
            self.worker_supervisor.shutdown(grace_seconds)
            self.worker_supervisor = None
        drained = self.drain_financial_events(grace_seconds)
        if self._broker_event_pump is not None:
            self._broker_event_pump.stop()
            self._broker_event_pump = None
        return drained

    def shutdown(self) -> None:
        self.stop_new_entries()
        writer = self._writer
        try:
            self.shutdown_workers()
            if writer is not None:
                writer.close()
        finally:
            self._writer = None
            self._reader = None
            self._coordinator = None
            self._backup_service = None
            self._reconciliation_coordinator = None
            self._reconciliation_report = None
            self._broker_event_processor = None
            self.worker = None
            self.instance_guard.release()
