from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, overload
from uuid import uuid4

from apps.core.broker_resilience import (
    BrokerCircuitOpenError,
    BrokerErrorContext,
    BrokerName,
    BrokerResilienceService,
)
from apps.core.health import HealthGate
from apps.core.risk import PersistedActiveExposurePort, RiskLedger
from apps.core.worker_client import DeliveryCertainty, OrderSubmissionPort, WorkerDispatchError
from packages.domain.models import Broker, OrderCommand, OrderRequest, WorkerOutcome, utc_now
from packages.persistence.health import DatabaseFailureReason
from packages.persistence.writer import (
    AccountBusyError,
    FinancialUnitOfWork,
    ManifestMonitorPendingError,
    PersistenceError,
    RiskLimitExceededError,
    SingleDatabaseWriter,
)
from packages.protocol.errors import ProtocolErrorCode
from packages.protocol.messages import WorkerSubmissionResult


class MultiBrokerSubmissionRouter(OrderSubmissionPort):
    """Routes order submission commands to the appropriate broker worker port."""

    def __init__(
        self,
        workers: Mapping[Broker | str, OrderSubmissionPort] | None = None,
    ) -> None:
        self._workers: dict[str, OrderSubmissionPort] = {}
        if workers is not None:
            for b, w in workers.items():
                self.register(b, w)

    def register(self, broker: Broker | str, worker: OrderSubmissionPort) -> None:
        key = broker.value if isinstance(broker, Broker) else str(broker).upper()
        self._workers[key] = worker

    def unregister(self, broker: Broker | str) -> None:
        key = broker.value if isinstance(broker, Broker) else str(broker).upper()
        self._workers.pop(key, None)

    def submit_order(self, command: OrderCommand) -> WorkerSubmissionResult:
        key = (
            command.broker.value
            if isinstance(command.broker, Broker)
            else str(command.broker).upper()
        )
        worker = self._workers.get(key)
        if worker is None:
            raise WorkerDispatchError(
                ProtocolErrorCode.WORKER_NOT_READY,
                DeliveryCertainty.NOT_SENT,
                f"no worker registered for broker {key}",
            )
        return worker.submit_order(command)


class AccountCommandSerializer:
    """Explicit per broker/account critical section; DB constraints remain the backstop."""

    def __init__(self) -> None:
        self._registry_lock = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}

    @contextmanager
    def serialize(self, broker: str, account_id: str) -> Iterator[None]:
        key = (broker.upper(), str(account_id))
        with self._registry_lock:
            account_lock = self._locks.setdefault(key, threading.Lock())
        with account_lock:
            yield


class EntryAuthorizationPort(Protocol):
    """Reduced auth boundary intentionally excluding every authentication secret."""

    def ensure_new_entry_allowed(
        self,
        broker: Broker,
        strategy_id: str,
        strategy_version: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class PersistedOrder:
    intent_id: str
    reservation_id: str
    message_id: str
    order_id: str


class OutboxDispatcher:
    def __init__(
        self,
        writer: SingleDatabaseWriter,
        worker: OrderSubmissionPort,
        health_gate: HealthGate,
        resilience_service: BrokerResilienceService | None = None,
    ) -> None:
        self._writer = writer
        self._worker = worker
        self._health_gate = health_gate
        self._resilience = resilience_service or BrokerResilienceService()

    def dispatch_next(
        self,
        broker: str | None = None,
        account_id: str | None = None,
        now: datetime | None = None,
    ) -> OrderCommand | None:
        dispatch_time = now or utc_now()
        command = self._writer.claim_next_message(
            dispatch_time, broker=broker, account_id=account_id
        )
        if command is None:
            return None
        if command.deadline_at <= dispatch_time:
            self._writer.cancel_expired_before_dispatch(command, dispatch_time)
            return command
        return self.dispatch_command(command)

    def dispatch_command(self, command: OrderCommand) -> OrderCommand:
        # 1. HealthGate (circuit breaker financeiro - autoridade máxima)
        allowed, blocker = self._health_gate.can_enter_order(
            command.broker.value,
            command.account_id,
        )
        if not allowed:
            self._writer.record_dispatch_not_sent(
                command,
                reason_code=blocker or "HG_ENTRY_NOT_AUTHORIZED",
                now=utc_now(),
            )
            return command

        # 2. Broker Resilience (opt-in via feature flag BROKER_RESILIENCE_ENABLED)
        resilience_ctx = BrokerErrorContext(
            broker=BrokerName.from_value(command.broker.value),
            operation="submit_order",
            trade_id=command.correlation_id,
            idempotency_key=command.message_id,
        )
        try:
            self._resilience.before_call(
                command.broker.value,
                "submit_order",
                resilience_ctx,
            )
        except BrokerCircuitOpenError:
            with suppress(PersistenceError):
                self._writer.record_dispatch_not_sent(
                    command,
                    reason_code="BROKER_CIRCUIT_OPEN",
                    now=utc_now(),
                )
            return command

        # 3. Submit ao worker (envolvido com observabilidade de resiliência)
        reason_code: str | None = None
        try:
            result = self._worker.submit_order(command)
        except WorkerDispatchError as exc:
            err_ctx = BrokerErrorContext(
                broker=resilience_ctx.broker,
                operation=resilience_ctx.operation,
                trade_id=resilience_ctx.trade_id,
                idempotency_key=resilience_ctx.idempotency_key,
                broker_code=exc.code.value,
                exception_type=type(exc).__name__,
                raw_message=str(exc),
            )
            self._resilience.on_error(err_ctx, exc)
            if exc.delivery is DeliveryCertainty.NOT_SENT:
                with suppress(PersistenceError):
                    self._writer.record_dispatch_not_sent(
                        command,
                        reason_code=exc.code.value,
                        now=utc_now(),
                    )
                self._health_gate.block_scope(
                    command.broker.value, command.account_id, "HG_WORKER_NOT_READY"
                )
                return command
            elif exc.delivery is DeliveryCertainty.POSSIBLY_SENT:
                outcome = WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND
                broker_order_id = None
                reason_code = exc.code.value
            else:
                outcome = WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND
                broker_order_id = None
                reason_code = exc.code.value
        except Exception as exc:
            # Once submit() is invoked, lack of a result is not evidence of non-delivery.
            self._resilience.on_error(resilience_ctx, exc)
            outcome = WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND
            broker_order_id = None
        else:
            self._resilience.on_success(
                command.broker.value,
                "submit_order",
                resilience_ctx,
                result,
            )
            outcome = result.outcome
            broker_order_id = result.broker_order_id
            reason_code = result.reason_code

        with suppress(PersistenceError):
            self._writer.record_dispatch_result(
                command,
                outcome.value,
                broker_order_id=broker_order_id,
                reason_code=reason_code,
                now=utc_now(),
            )

        if outcome is WorkerOutcome.TIMEOUT_AFTER_POSSIBLE_SEND:
            self._health_gate.block_scope(
                command.broker.value, command.account_id, "HG_ORDER_UNKNOWN"
            )
        return command


class OrderCoordinator:
    """Consumes an already arbitrated/allocated request and owns the financial path."""

    def __init__(
        self,
        writer: SingleDatabaseWriter,
        worker: OrderSubmissionPort | Mapping[Broker | str, OrderSubmissionPort] | HealthGate,
        health_gate: HealthGate | OrderSubmissionPort,
        resilience_service: BrokerResilienceService | None = None,
        *,
        serializer: AccountCommandSerializer | None = None,
        risk_ledger: RiskLedger | None = None,
        entry_authorizer: EntryAuthorizationPort | None = None,
    ) -> None:
        actual_health_gate: HealthGate
        submission_worker: OrderSubmissionPort
        raw_worker: Any
        if isinstance(worker, HealthGate) and not isinstance(health_gate, HealthGate):
            actual_health_gate = worker
            raw_worker = health_gate
        else:
            actual_health_gate = health_gate  # type: ignore[assignment]
            raw_worker = worker

        if isinstance(raw_worker, Mapping):
            submission_worker = MultiBrokerSubmissionRouter(raw_worker)
        else:
            submission_worker = raw_worker

        self._writer = writer
        self._uow = FinancialUnitOfWork(writer)
        self._health_gate = actual_health_gate
        self._resilience = resilience_service or BrokerResilienceService()
        self._dispatcher = OutboxDispatcher(
            writer,
            submission_worker,
            actual_health_gate,
            resilience_service=self._resilience,
        )
        self._serializer = serializer or AccountCommandSerializer()
        self._risk_ledger = risk_ledger or RiskLedger()
        if not self._risk_ledger.has_active_exposure_port:
            self._risk_ledger.configure_active_exposure_port(PersistedActiveExposurePort(writer))
        self._entry_authorizer = entry_authorizer

    @property
    def resilience(self) -> BrokerResilienceService:
        return self._resilience

    @property
    def resilience_service(self) -> BrokerResilienceService:
        return self._resilience

    @overload
    def submit(
        self,
        request: OrderCommand,
        *,
        dispatch: bool = ...,
        pre_persist: Callable[[OrderRequest], None] | None = ...,
        on_persisted: Callable[[str, str], None] | None = ...,
    ) -> OrderCommand: ...

    @overload
    def submit(
        self,
        request: OrderRequest,
        *,
        dispatch: bool = ...,
        pre_persist: Callable[[OrderRequest], None] | None = ...,
        on_persisted: Callable[[str, str], None] | None = ...,
    ) -> PersistedOrder: ...

    def submit(
        self,
        request: OrderRequest | OrderCommand,
        *,
        dispatch: bool = True,
        pre_persist: Callable[[OrderRequest], None] | None = None,
        on_persisted: Callable[[str, str], None] | None = None,
    ) -> PersistedOrder | OrderCommand:
        if isinstance(request, OrderCommand):
            return self._dispatcher.dispatch_command(request)

        self._risk_ledger.refresh_digit_health_gate(self._health_gate)
        if self._entry_authorizer is not None:
            self._entry_authorizer.ensure_new_entry_allowed(
                request.broker,
                request.strategy_id,
                request.strategy_version,
            )
        self._health_gate.ensure_open(request.broker.value, request.account_id)
        with self._serializer.serialize(request.broker.value, request.account_id):
            if pre_persist is not None:
                pre_persist(request)
            self._risk_ledger.reserve(request, self._health_gate)
            created_at = utc_now()
            intent_id = str(uuid4())
            reservation_id = str(uuid4())
            message_id = str(uuid4())
            order_id = str(uuid4())
            command = OrderCommand(
                message_id=message_id,
                correlation_id=request.correlation_id,
                intent_id=intent_id,
                order_id=order_id,
                broker=request.broker,
                account_id=request.account_id,
                product=request.product,
                symbol=request.symbol,
                direction=request.direction,
                amount=request.amount,
                deadline_at=request.deadline_at,
                duration=request.duration,
                duration_unit=request.duration_unit,
                prediction_digit=request.prediction_digit,
                contract_expiry_at=request.contract_expiry_at,
            )
            try:
                self._uow.persist(
                    request=request,
                    command=command,
                    intent_id=intent_id,
                    reservation_id=reservation_id,
                    order_id=order_id,
                    created_at=created_at,
                    global_max_exposure_minor_units=self._risk_ledger.config.global_max_exposure_minor_units,
                    max_exposure_per_symbol_minor_units=self._risk_ledger.config.max_exposure_per_symbol_minor_units,
                    reference_currency=self._risk_ledger.config.reference_currency,
                )
            except AccountBusyError:
                raise
            except ManifestMonitorPendingError:
                raise
            except RiskLimitExceededError:
                raise
            except PersistenceError:
                self._health_gate.fail_database(DatabaseFailureReason.DB_WRITE_FAILED)
                raise
            if on_persisted is not None:
                on_persisted(request.strategy_id, order_id)
            if dispatch:
                self._dispatcher.dispatch_next(
                    broker=request.broker.value, account_id=request.account_id
                )
            return PersistedOrder(
                intent_id=intent_id,
                reservation_id=reservation_id,
                message_id=message_id,
                order_id=order_id,
            )

    def dispatch_pending(
        self,
        broker: str | None = None,
        account_id: str | None = None,
    ) -> OrderCommand | None:
        self._health_gate.ensure_open(broker, account_id)
        return self._dispatcher.dispatch_next(broker=broker, account_id=account_id)
