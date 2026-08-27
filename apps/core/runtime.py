from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from apps.core.broker_events import BrokerEventProcessor, BrokerEventPump, OrderEventSource
from apps.core.coordinator import (
    EntryAuthorizationPort,
    MultiBrokerSubmissionRouter,
    OrderCoordinator,
    PersistedOrder,
)
from apps.core.digit_risk_config import DigitRiskConfig
from apps.core.digit_risk_store import DigitRiskConfigStore
from apps.core.health import HealthGate
from apps.core.instance import CoreInstanceGuard, CoreInstanceGuardError
from apps.core.reconciliation import (
    ReconciliationCoordinator,
    ReconciliationOutcome,
    ReconciliationReport,
)
from apps.core.recovery import RecoveryCoordinator, RecoveryReport
from apps.core.risk import RiskLedger
from apps.core.worker_client import OrderStatusPort, OrderSubmissionPort, WorkerPort
from apps.core.worker_supervisor import WorkerSupervisor
from apps.simulated_worker.scenarios import WorkerScenario
from packages.domain.models import Broker, BrokerOrderEvent, OrderCommand, OrderRequest
from packages.observability.events import EventSink, PersistentJsonlEventSink
from packages.persistence.backup import DatabaseBackupService
from packages.persistence.health import DatabaseFailureReason, DatabaseHealth
from packages.persistence.reader import StateReader
from packages.persistence.writer import BrokerEventApplyResult, SingleDatabaseWriter


class FinancialWorkerPort(OrderSubmissionPort, OrderStatusPort, OrderEventSource, Protocol):
    pass


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
        deferred_reconciliation_brokers: frozenset[Broker] = frozenset(),
    ) -> None:
        self.profile_directory = profile_directory
        self.database_path = profile_directory / "state.db"
        self._digit_risk_config_store = DigitRiskConfigStore(
            profile_directory / "digit_risk_config.json"
        )
        self.event_sink = event_sink or PersistentJsonlEventSink(
            profile_directory / "operational-journal.jsonl"
        )
        self.database_health = DatabaseHealth()
        self.health_gate = HealthGate(self.database_health, self.event_sink)
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
        self._deferred_reconciliation_brokers = deferred_reconciliation_brokers
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
        self._deriv_event_pump: BrokerEventPump | None = None
        self._submission_router: MultiBrokerSubmissionRouter | None = None

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
            risk_ledger = RiskLedger(
                digit_config=self._digit_risk_config_store.load(),
                digit_runtime_expirer=writer.expire_digit_cooldown,
            )
            writer.configure_digit_risk_runtime(risk_ledger.digit_runtime_policy())
            risk_ledger.restore_digit_runtime(writer.expire_digit_cooldown())
            risk_ledger.restore(reader.list_by_state("risk_reservations", "ACTIVE"))
            self._risk_ledger = risk_ledger
            worker = self.worker
            if worker is None:
                supervisor = WorkerSupervisor(
                    self.health_gate,
                    scenario=self.worker_scenario,
                    event_sink=self.event_sink,
                    broker_store_path=self.simulated_broker_store_path,
                    event_queue_size=self.worker_event_queue_size,
                    on_disconnected=self.stop_new_entries,
                    on_recovered=self._recover_simulated_financial_state,
                )
                supervisor.start()
                worker = supervisor
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
            immediate_candidates = tuple(
                candidate
                for candidate in reader.list_reconciliation_candidates()
                if Broker(str(candidate["broker"])) not in self._deferred_reconciliation_brokers
            )
            if immediate_candidates:
                self._reconciliation_report = ReconciliationReport(
                    tuple(
                        reconciliation.reconcile_order(str(candidate["order_id"]))
                        for candidate in immediate_candidates
                    )
                )
            else:
                self._reconciliation_report = ReconciliationReport(())
            submission_router = MultiBrokerSubmissionRouter(
                {
                    Broker.DERIV: worker,
                    Broker.IQ_OPTION: worker,
                }
            )
            self._submission_router = submission_router
            coordinator = OrderCoordinator(
                writer,
                submission_router,
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
                self._deriv_event_pump = None
                self._submission_router = None
                self.worker = None
                self.instance_guard.release()
            raise

    def _recover_simulated_financial_state(self) -> None:
        """Reconcile a replaced simulated connection without rearming entries."""

        reconciliation = self._reconciliation_coordinator
        if reconciliation is None or self._reader is None:
            return
        candidates = tuple(
            item
            for item in self._reader.list_reconciliation_candidates()
            if Broker(str(item["broker"])) not in self._deferred_reconciliation_brokers
        )
        outcomes = tuple(
            reconciliation.reconcile_order(str(item["order_id"])) for item in candidates
        )
        prior = () if self._reconciliation_report is None else self._reconciliation_report.results
        self._reconciliation_report = ReconciliationReport((*prior, *outcomes))
        if self._risk_ledger is not None:
            self._risk_ledger.restore(self._reader.list_by_state("risk_reservations", "ACTIVE"))
        self.event_sink.emit(
            "simulated_worker_recovery_reconciled",
            reconciled_count=len(outcomes),
            reason_code="OPERATOR_REARM_REQUIRED",
        )

    def submit(self, request: OrderRequest, *, dispatch: bool = True) -> PersistedOrder:
        return self.coordinator.submit(request, dispatch=dispatch)

    def update_digit_risk_config(
        self,
        config: DigitRiskConfig,
    ) -> tuple[bool, str | None]:
        """Apply and durably retain the Core-authoritative operator configuration."""

        if self.reader.has_nonterminal_deriv_digit_order():
            return False, "DIGIT_RISK_CONFIG_BLOCKED_OPEN_ORDER"
        previous = self.risk_ledger.digit_config
        accepted, reason = self.risk_ledger.update_digit_risk_config(config, self.health_gate)
        if not accepted:
            return False, reason
        try:
            self.writer.configure_digit_risk_runtime(self.risk_ledger.digit_runtime_policy())
            self._digit_risk_config_store.save(config)
        except (OSError, RuntimeError, ValueError):
            self.risk_ledger.update_digit_risk_config(previous, self.health_gate)
            self.writer.configure_digit_risk_runtime(self.risk_ledger.digit_runtime_policy())
            return False, "DIGIT_RISK_CONFIG_PERSISTENCE_FAILED"
        return True, None

    def attach_deriv_worker(
        self,
        worker: FinancialWorkerPort,
        *,
        on_order_event: Callable[[BrokerOrderEvent, BrokerEventApplyResult], None] | None = None,
    ) -> None:
        """Route Deriv orders and settlement events through the authenticated worker."""

        router = self._submission_router
        processor = self._broker_event_processor
        if router is None or processor is None:
            raise RuntimeError("Core runtime is not started")
        self.detach_deriv_worker()
        self.reconcile_deriv_worker(worker)
        router.register(Broker.DERIV, worker)
        pump = BrokerEventPump(
            worker,
            processor,
            self.health_gate,
            self.event_sink,
            on_processed=on_order_event,
        )
        self._deriv_event_pump = pump
        pump.start()

    def reconcile_deriv_worker(self, worker: OrderStatusPort) -> None:
        """Resolve durable Deriv ambiguity before enabling a submission route."""

        if Broker.DERIV in self._deferred_reconciliation_brokers:
            deferred = tuple(
                candidate
                for candidate in self.reader.list_reconciliation_candidates()
                if str(candidate["broker"]) == Broker.DERIV.value
            )
            if deferred:
                reconciliation = ReconciliationCoordinator(
                    self.writer,
                    self.reader,
                    worker,
                    self.health_gate,
                    self.event_sink,
                    query_timeout=3.0,
                )
                prior = (
                    ()
                    if self._reconciliation_report is None
                    else self._reconciliation_report.results
                )
                resolved = tuple(
                    reconciliation.reconcile_order(str(candidate["order_id"]))
                    for candidate in deferred
                )
                self._reconciliation_report = ReconciliationReport((*prior, *resolved))
                self.risk_ledger.restore(self.reader.list_by_state("risk_reservations", "ACTIVE"))
                self.risk_ledger.restore_digit_runtime(self.writer.expire_digit_cooldown())
                if not self.reader.list_reconciliation_candidates():
                    self.health_gate.clear_if("HG_ORDER_UNKNOWN")
                    self.health_gate.clear_if("HG_RECONCILIATION_REQUIRED")
                    self.health_gate.clear_if("HG_RECONCILIATION_UNAVAILABLE")
                    self.dispatcher_started = self.health_gate.state.is_open

    def detach_deriv_worker(self) -> None:
        pump = self._deriv_event_pump
        self._deriv_event_pump = None
        if pump is not None:
            pump.stop()
        router = self._submission_router
        if router is not None:
            router.unregister(Broker.DERIV)

    def dispatch_pending(self) -> OrderCommand | None:
        if not self.dispatcher_started:
            raise RuntimeError("dispatcher is not started")
        return self.coordinator.dispatch_pending()

    def stop_new_entries(self) -> None:
        self.dispatcher_started = False
        self.health_gate.block("HG_SAFE_STOP")
        self.event_sink.emit("trading_disarmed", reason_code="HG_SAFE_STOP")

    @property
    def safe_stop_active(self) -> bool:
        return self.health_gate.contains("HG_SAFE_STOP")

    def resume_new_entries(self) -> bool:
        """Clear only the operator safe stop; every other blocker remains authoritative."""

        if self._writer is None:
            raise RuntimeError("Core runtime is not started")
        # A cooldown can expire while the bot is paused. Refresh the durable,
        # time-dependent risk state before evaluating ARM; otherwise the old
        # HG_COOLDOWN_ACTIVE blocker survives indefinitely until an unrelated
        # UI projection or entry check happens to clear it.
        self.risk_ledger.refresh_digit_health_gate(self.health_gate)
        self.health_gate.clear_if("HG_SAFE_STOP")
        self.dispatcher_started = self.health_gate.state.is_open
        self.event_sink.emit(
            "trading_arm_evaluated",
            armed=self.dispatcher_started,
            reason_code=self.health_gate.state.reason_code,
        )
        return self.dispatcher_started

    def drain_financial_events(self, timeout: float) -> bool:
        """Drain events already accepted by IPC without waiting for future broker outcomes."""

        if timeout <= 0:
            raise ValueError("drain timeout must be positive")
        pumps = tuple(
            pump for pump in (self._broker_event_pump, self._deriv_event_pump) if pump is not None
        )
        if not pumps:
            return True
        per_pump_timeout = max(0.01, timeout / len(pumps))
        return all(pump.drain(per_pump_timeout) for pump in pumps)

    @property
    def pending_financial_event_count(self) -> int:
        return sum(
            pump.pending_event_count
            for pump in (self._broker_event_pump, self._deriv_event_pump)
            if pump is not None
        )

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
        self.detach_deriv_worker()
        return drained

    def shutdown(self) -> None:
        self.event_sink.emit("core_shutdown_started")
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
            self._submission_router = None
            self.worker = None
            self.instance_guard.release()
            self.event_sink.emit("core_shutdown_completed")
