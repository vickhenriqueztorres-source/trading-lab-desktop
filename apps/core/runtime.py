from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
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
    MultiBrokerStatusRouter,
    ReconciliationCoordinator,
    ReconciliationOutcome,
    ReconciliationReport,
)
from apps.core.reconciliation_scheduler import ReconciliationScheduler
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
from packages.persistence.writer import (
    BrokerEventApplyResult,
    PersistenceError,
    SingleDatabaseWriter,
)


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
        digit_account_type: str | None = None,
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
        self._digit_account_type = digit_account_type
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
        self._reconciliation_scheduler: ReconciliationScheduler | None = None
        self._reconciliation_status_router: MultiBrokerStatusRouter | None = None
        self._reconciliation_report: ReconciliationReport | None = None
        self._risk_ledger: RiskLedger | None = None
        self._broker_event_processor: BrokerEventProcessor | None = None
        self._broker_event_pump: BrokerEventPump | None = None
        self._deriv_event_pump: BrokerEventPump | None = None
        self._iqoption_event_pump: BrokerEventPump | None = None
        self._deriv_reconciliation_completed: Callable[[], None] | None = None
        self._submission_router: MultiBrokerSubmissionRouter | None = None
        self.iqoption_entry_validator: Callable[[OrderRequest], None] | None = None
        self.iqoption_execution_lock: AbstractContextManager[object] = nullcontext()
        self.iqoption_order_registered: Callable[[str, str], None] | None = None

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
                digit_config=self._digit_risk_config_store.load(
                    account_type=self._digit_account_type
                ),
                digit_runtime_expirer=writer.expire_digit_cooldown,
            )
            self._configure_digit_risk_runtime_for_startup(writer, reader, risk_ledger)
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
            status_router = MultiBrokerStatusRouter()
            for broker in Broker:
                if broker not in self._deferred_reconciliation_brokers:
                    status_router.register(broker, worker)
            self._reconciliation_status_router = status_router
            reconciliation = ReconciliationCoordinator(
                writer,
                reader,
                status_router,
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
            scheduler = ReconciliationScheduler(
                reconciliation,
                reader,
                self.health_gate,
                self.event_sink,
                on_cycle_completed=self._on_reconciliation_cycle_completed,
            )
            self._reconciliation_scheduler = scheduler
            scheduler.start()
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
                self._reconciliation_scheduler = None
                self._reconciliation_status_router = None
                self._reconciliation_report = None
                self._broker_event_processor = None
                self._broker_event_pump = None
                self._deriv_event_pump = None
                self._submission_router = None
            self.worker = None
            self.instance_guard.release()
            raise

    def _configure_digit_risk_runtime_for_startup(
        self,
        writer: SingleDatabaseWriter,
        reader: StateReader,
        risk_ledger: RiskLedger,
    ) -> None:
        """Keep startup recoverable when a flat Demo sequence used an older config.

        Operator reconfiguration is intentionally blocked mid-Martingale unless it
        explicitly resets the Demo recovery sequence. A previous build could persist
        the new UI config while leaving an old flat sequence in ``digit_risk_runtime``;
        treating that as fatal on the next startup made the EXE close before the UI.
        Startup may clear only this stale test sequence, and only when there is no
        non-terminal Deriv digit order and no ACTIVE risk reservation to reconcile.
        """

        policy = risk_ledger.digit_runtime_policy()
        try:
            writer.configure_digit_risk_runtime(policy)
            return
        except PersistenceError as exc:
            if "DIGIT_MARTINGALE_SEQUENCE_ACTIVE" not in str(exc):
                raise

        if reader.has_nonterminal_deriv_digit_order() or reader.list_by_state(
            "risk_reservations",
            "ACTIVE",
        ):
            raise PersistenceError("DIGIT_MARTINGALE_SEQUENCE_ACTIVE_WITH_EXPOSURE")

        writer.configure_digit_risk_runtime(policy, reset_active_sequence=True)
        self.event_sink.emit(
            "digit_runtime_startup_sequence_reset",
            reason_code="DIGIT_RUNTIME_POLICY_MISMATCH_FLAT",
        )

    def _recover_simulated_financial_state(self) -> None:
        """Reconcile a replaced simulated connection without rearming entries."""

        reconciliation = self._reconciliation_coordinator
        if reconciliation is None or self._reader is None:
            return
        scheduler = self._reconciliation_scheduler
        if scheduler is not None:
            scheduler.trigger("worker_reconnected")
        self.event_sink.emit(
            "simulated_worker_recovery_reconciled",
            reconciled_count=0,
            reason_code="OPERATOR_REARM_REQUIRED",
        )

    def _on_reconciliation_cycle_completed(self, report: ReconciliationReport) -> None:
        prior = () if self._reconciliation_report is None else self._reconciliation_report.results
        self._reconciliation_report = ReconciliationReport((*prior, *report.results))
        if self._risk_ledger is not None and self._reader is not None:
            self._risk_ledger.restore(self._reader.list_by_state("risk_reservations", "ACTIVE"))
        self.dispatcher_started = self.health_gate.state.is_open
        callback = getattr(self, "_deriv_reconciliation_completed", None)
        if callback is not None:
            callback()

    def submit(self, request: OrderRequest, *, dispatch: bool = True) -> PersistedOrder:
        if self.iqoption_entry_validator is not None and request.broker is Broker.IQ_OPTION:
            with self.iqoption_execution_lock:
                result = self.coordinator.submit(
                    request,
                    dispatch=dispatch,
                    pre_persist=self.iqoption_entry_validator,
                    on_persisted=self.iqoption_order_registered
                    if request.manifest_context is not None
                    else None,
                )
                return result
        return self.coordinator.submit(request, dispatch=dispatch)

    def update_digit_risk_config(
        self,
        config: DigitRiskConfig,
    ) -> tuple[bool, str | None]:
        """Apply and durably retain the Core-authoritative operator configuration."""

        if self.reader.has_nonterminal_deriv_digit_order():
            return False, "DIGIT_RISK_CONFIG_BLOCKED_OPEN_ORDER"
        previous = self.risk_ledger.digit_config
        accepted, reason = self.risk_ledger.update_digit_risk_config(
            config,
            self.health_gate,
            reset_active_sequence=True,
        )
        if not accepted:
            return False, reason
        try:
            self.writer.configure_digit_risk_runtime(
                self.risk_ledger.digit_runtime_policy(),
                reset_active_sequence=True,
            )
            self._digit_risk_config_store.save(config)
        except (OSError, RuntimeError, ValueError):
            self.risk_ledger.update_digit_risk_config(previous, self.health_gate)
            self.writer.configure_digit_risk_runtime(self.risk_ledger.digit_runtime_policy())
            return False, "DIGIT_RISK_CONFIG_PERSISTENCE_FAILED"
        return True, None

    def reset_digit_test_session(self) -> tuple[bool, str | None]:
        """Start a fresh bounded test session while preserving the order history."""

        if self.reader.has_nonterminal_deriv_digit_order():
            return False, "DIGIT_TEST_SESSION_RESET_BLOCKED_EXPOSURE"
        try:
            self.writer.reset_digit_test_session_if_flat()
        except PersistenceError as exc:
            reason = str(exc)
            if "DIGIT_TEST_SESSION_RESET_BLOCKED_EXPOSURE" in reason:
                return False, "DIGIT_TEST_SESSION_RESET_BLOCKED_EXPOSURE"
            return False, "DIGIT_TEST_SESSION_RESET_FAILED"
        self.risk_ledger.reset_daily_pnl(self.health_gate)
        self.event_sink.emit("digit_test_session_reset", reason_code="OPERATOR_DEMO_RESET")
        return True, None

    def reset_digit_recovery_state(self) -> tuple[bool, str | None]:
        """Persistently clear transient digit cooldown/Martingale state.

        Order history and the daily P&L remain intact.  The writer performs the
        reset in one transaction so a restart cannot resurrect the old sequence.
        """

        if self.reader.has_nonterminal_deriv_digit_order():
            return False, "DIGIT_RECOVERY_RESET_BLOCKED_EXPOSURE"
        try:
            self.writer.configure_digit_risk_runtime(
                self.risk_ledger.digit_runtime_policy(),
                reset_active_sequence=True,
            )
            self.risk_ledger.reset_digit_recovery_state(self.health_gate)
        except (PersistenceError, RuntimeError, ValueError):
            return False, "DIGIT_RECOVERY_RESET_FAILED"
        self.event_sink.emit(
            "digit_operator_recovery_reset",
            reason_code="OPERATOR_MANUAL_RESUME",
        )
        return True, None

    def attach_deriv_worker(
        self,
        worker: FinancialWorkerPort,
        *,
        on_order_event: Callable[[BrokerOrderEvent, BrokerEventApplyResult], None] | None = None,
        on_reconciliation_completed: Callable[[], None] | None = None,
    ) -> None:
        """Route Deriv orders and settlement events through the authenticated worker."""

        router = self._submission_router
        processor = self._broker_event_processor
        if router is None or processor is None:
            raise RuntimeError("Core runtime is not started")
        self.detach_deriv_worker()
        self._deriv_reconciliation_completed = on_reconciliation_completed
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
            router = self._reconciliation_status_router
            scheduler = self._reconciliation_scheduler
            if router is None or scheduler is None:
                raise RuntimeError("reconciliation scheduler is unavailable")
            router.register(Broker.DERIV, worker)
            scheduler.trigger("deriv_reconnected")

    def detach_deriv_worker(self) -> None:
        self._deriv_reconciliation_completed = None
        pump = self._deriv_event_pump
        self._deriv_event_pump = None
        if pump is not None:
            pump.stop()
        router = self._submission_router
        if router is not None:
            router.unregister(Broker.DERIV)
        status_router = self._reconciliation_status_router
        if status_router is not None and Broker.DERIV in self._deferred_reconciliation_brokers:
            status_router.unregister(Broker.DERIV)

    def attach_iqoption_worker(
        self,
        worker: FinancialWorkerPort,
        *,
        on_order_event: Callable[[BrokerOrderEvent, BrokerEventApplyResult], None] | None = None,
        on_reconciliation_completed: Callable[[], None] | None = None,
    ) -> None:
        """Route Practice IQ orders through the persistent Core financial path."""

        router = self._submission_router
        processor = self._broker_event_processor
        if router is None or processor is None:
            raise RuntimeError("Core runtime is not started")
        self.detach_iqoption_worker()
        self.reconcile_iqoption_worker(worker)
        router.register(Broker.IQ_OPTION, worker)
        pump = BrokerEventPump(
            worker,
            processor,
            self.health_gate,
            self.event_sink,
            on_processed=on_order_event,
        )
        self._iqoption_event_pump = pump
        pump.start()
        if on_reconciliation_completed is not None:
            on_reconciliation_completed()

    def reconcile_iqoption_worker(self, worker: OrderStatusPort) -> None:
        if Broker.IQ_OPTION not in self._deferred_reconciliation_brokers:
            return
        router = self._reconciliation_status_router
        scheduler = self._reconciliation_scheduler
        if router is None or scheduler is None:
            raise RuntimeError("reconciliation scheduler is unavailable")
        router.register(Broker.IQ_OPTION, worker)
        scheduler.trigger("iqoption_reconnected")

    def detach_iqoption_worker(self) -> None:
        pump = self._iqoption_event_pump
        self._iqoption_event_pump = None
        if pump is not None:
            pump.stop()
        router = self._submission_router
        if router is not None:
            router.unregister(Broker.IQ_OPTION)
        status_router = self._reconciliation_status_router
        if status_router is not None and Broker.IQ_OPTION in self._deferred_reconciliation_brokers:
            status_router.unregister(Broker.IQ_OPTION)

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
        blocker = self.health_gate.state.reason_code
        if not self.dispatcher_started:
            # A rejected ARM attempt must remain visibly DISARMED. Otherwise the UI
            # projects the bot as enabled while a different risk gate blocks every order.
            self.health_gate.block("HG_SAFE_STOP")
        self.event_sink.emit(
            "trading_arm_evaluated",
            armed=self.dispatcher_started,
            reason_code=blocker,
        )
        return self.dispatcher_started

    def resume_new_entries_for(self, broker: Broker, account_id: str) -> bool:
        """Arm one broker/account without inheriting another broker's blockers."""

        if self._writer is None:
            raise RuntimeError("Core runtime is not started")
        self.risk_ledger.refresh_digit_health_gate(self.health_gate)
        self.health_gate.clear_if("HG_SAFE_STOP")
        scoped_state = self.health_gate.state_for(broker.value, account_id)
        armed = scoped_state.is_open
        if armed:
            self.dispatcher_started = True
        self.event_sink.emit(
            "trading_arm_evaluated",
            armed=armed,
            broker=broker.value,
            account_id=account_id,
            reason_code=scoped_state.reason_code,
        )
        return armed

    def drain_financial_events(self, timeout: float) -> bool:
        """Drain events already accepted by IPC without waiting for future broker outcomes."""

        if timeout <= 0:
            raise ValueError("drain timeout must be positive")
        pumps = tuple(
            pump
            for pump in (
                self._broker_event_pump,
                self._deriv_event_pump,
                self._iqoption_event_pump,
            )
            if pump is not None
        )
        if not pumps:
            return True
        per_pump_timeout = max(0.01, timeout / len(pumps))
        return all(pump.drain(per_pump_timeout) for pump in pumps)

    @property
    def pending_financial_event_count(self) -> int:
        return sum(
            pump.pending_event_count
            for pump in (
                self._broker_event_pump,
                self._deriv_event_pump,
                self._iqoption_event_pump,
            )
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
        self.detach_iqoption_worker()
        return drained

    def shutdown(self) -> None:
        self.event_sink.emit("core_shutdown_started")
        self.stop_new_entries()
        writer = self._writer
        try:
            scheduler = self._reconciliation_scheduler
            self._reconciliation_scheduler = None
            if scheduler is not None:
                scheduler.stop()
            self.shutdown_workers()
            if writer is not None:
                writer.close()
        finally:
            self._writer = None
            self._reader = None
            self._coordinator = None
            self._backup_service = None
            self._reconciliation_coordinator = None
            self._reconciliation_status_router = None
            self._reconciliation_report = None
            self._broker_event_processor = None
            self._submission_router = None
            self.worker = None
            self.instance_guard.release()
            self.event_sink.emit("core_shutdown_completed")
