from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from apps.core.candle_pipeline import CoreCandlePipeline, market_candle_from_closed
from apps.core.coordinator import PersistedOrder
from apps.core.risk import RiskLedger
from apps.core.strategy_pipeline import EntryPlan, StrategyEntryPipeline
from packages.audit import DecisionEventType, DecisionJournal, DecisionRecord
from packages.domain.models import OrderRequest
from packages.market_data import (
    CandleEnvelope,
    CandleIngress,
    CandleIngressStatus,
    CandleStoreOutcome,
    ClosedCandle,
    InMemoryCandleStore,
)
from packages.persistence.candle_repository import SqliteCandleRepository
from packages.persistence.journal_repository import SqliteDecisionJournalRepository
from packages.persistence.replay_repository import ReplayConflictError, SqliteReplayRepository
from packages.persistence.strategy_commit_repository import (
    SqliteCandleDecisionCommitRepository,
)
from packages.persistence.warmup_repository import SqliteWarmupRepository
from packages.portfolio_allocation import BudgetSnapshot, PortfolioAllocator
from packages.replay.clock import ReplayClock
from packages.replay.models import (
    ReplayRecord,
    ReplayRequest,
    ReplayResult,
    ReplayRiskDecision,
)
from packages.replay.persistent_journal import PersistentDecisionJournal
from packages.signal_arbitration import SignalArbiter
from packages.strategies import (
    RuntimePhase,
    StrategyRuntimeManager,
    StrategyStateV1,
    WarmupCheckpoint,
)
from packages.strategy_catalog import StrategyCatalog, StrategyCatalogError


class CheckpointRestoreReason(StrEnum):
    MANIFEST_MISMATCH = "CHECKPOINT_MANIFEST_MISMATCH"
    CONFIG_MISMATCH = "CHECKPOINT_CONFIG_MISMATCH"
    HASH_INVALID = "CHECKPOINT_HASH_INVALID"
    CANDLE_MISSING = "CHECKPOINT_CANDLE_MISSING"
    STATE_VERSION_UNSUPPORTED = "CHECKPOINT_STATE_VERSION_UNSUPPORTED"
    CONTEXT_MISMATCH = "CHECKPOINT_CONTEXT_MISMATCH"
    JOURNAL_DIVERGED = "CHECKPOINT_JOURNAL_DIVERGED"


class CheckpointRestoreError(RuntimeError):
    def __init__(self, reason: CheckpointRestoreReason) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.reason_code = reason.value


class _JournalPort(Protocol):
    @property
    def records(self) -> tuple[DecisionRecord, ...]: ...

    @property
    def final_hash(self) -> str: ...

    def append(
        self,
        event_type: DecisionEventType,
        *,
        occurred_at: datetime,
        correlation_id: str,
        causation_id: str | None,
        strategy_id: str,
        strategy_version: str,
        manifest_hash: str,
        configuration_hash: str,
        candle_id: str,
        payload: tuple[tuple[str, str], ...],
    ) -> DecisionRecord: ...


@dataclass(frozen=True, slots=True)
class ReplayPersistence:
    candles: SqliteCandleRepository
    journal: SqliteDecisionJournalRepository
    replays: SqliteReplayRepository
    warmup: SqliteWarmupRepository
    commits: SqliteCandleDecisionCommitRepository


@dataclass(frozen=True, slots=True)
class _ReplayIntent:
    request: OrderRequest
    persisted: PersistedOrder


def _deterministic_id(run_id: str, kind: str, correlation_id: str) -> str:
    return hashlib.sha256(f"{run_id}|{kind}|{correlation_id}".encode()).hexdigest()


class _ReplayOrderIntentSink:
    """Replay-only Risk Ledger boundary with no worker, critical DB or dispatch capability."""

    def __init__(
        self,
        run_id: str,
        risk_ledger: RiskLedger,
        clock: ReplayClock,
        *,
        seen_correlations: set[str] | None = None,
    ) -> None:
        self._run_id = run_id
        self._risk_ledger = risk_ledger
        self._clock = clock
        self._seen = set(seen_correlations or ())
        self.intents: list[_ReplayIntent] = []

    def submit(self, request: OrderRequest, *, dispatch: bool = True) -> PersistedOrder:
        if dispatch:
            raise RuntimeError("replay cannot dispatch orders")
        if request.deadline_at <= self._clock.now():
            raise ValueError("replay order intent deadline is expired")
        if request.correlation_id in self._seen:
            raise RuntimeError("duplicate replay order intent correlation")
        self._risk_ledger.reserve(request)
        self._seen.add(request.correlation_id)
        persisted = PersistedOrder(
            intent_id=_deterministic_id(self._run_id, "intent", request.correlation_id),
            reservation_id=_deterministic_id(self._run_id, "reservation", request.correlation_id),
            message_id=_deterministic_id(self._run_id, "message", request.correlation_id),
            order_id=_deterministic_id(self._run_id, "order", request.correlation_id),
        )
        self.intents.append(_ReplayIntent(request, persisted))
        return persisted


class ReplaySession:
    def __init__(
        self,
        request: ReplayRequest,
        catalog: StrategyCatalog,
        *,
        max_candles: int,
        max_journal_events: int,
        persistence: ReplayPersistence | None,
        checkpoint: WarmupCheckpoint | None,
    ) -> None:
        self._request = request
        self._persistence = persistence
        self._expected_ids = {candle.candle_id for candle in request.candles}
        entry = catalog.get(request.strategy_id, request.strategy_version)
        self._warmup_candles = entry.manifest.warmup_candles
        self._recent_ids: deque[str] = deque(maxlen=self._warmup_candles)
        self._accepted_count = 0
        initial_ms = (
            checkpoint.last_close_time_ms
            if checkpoint is not None
            else min(candle.open_time_ms for candle in request.candles)
        )
        self._clock = ReplayClock(initial_ms)
        if persistence is None:
            self._journal: _JournalPort = DecisionJournal(
                request.run_id,
                max_events=max_journal_events,
            )
        else:
            self._journal = PersistentDecisionJournal(
                request.run_id,
                persistence.journal,
                max_events=max_journal_events,
            )
        self._signal_ids: list[str] = []
        self._arbitration_reasons: list[str] = []
        self._allocation_reasons: list[str] = []
        self._risk_decisions: list[ReplayRiskDecision] = []
        seen_correlations = self._hydrate_summary()
        self._runtimes = StrategyRuntimeManager(catalog)
        ingress_store = InMemoryCandleStore(max_candles=max_candles)
        if checkpoint is not None:
            self._restore(checkpoint, ingress_store)
        self._sink = _ReplayOrderIntentSink(
            request.run_id,
            RiskLedger(),
            self._clock,
            seen_correlations=seen_correlations,
        )
        self._core = CoreCandlePipeline(
            CandleIngress(ingress_store),
            StrategyEntryPipeline(
                self._runtimes,
                SignalArbiter(catalog),
                PortfolioAllocator(),
                self._sink,
            ),
        )

    def process(self, candle: ClosedCandle, *, dispatch: bool = False) -> None:
        if dispatch:
            raise RuntimeError("CAPABILITY_DENIED")
        if candle.candle_id not in self._expected_ids:
            raise ValueError("candle does not belong to replay request")
        if self._persistence is not None:
            self._persistence.candles.store(candle)
        persistent_journal = (
            self._journal if isinstance(self._journal, PersistentDecisionJournal) else None
        )
        if persistent_journal is not None:
            persistent_journal.begin_candle(candle.candle_id)
        try:
            accepted = self._evaluate_candle(candle)
            if persistent_journal is None:
                return
            if not accepted:
                persistent_journal.cancel_empty_candle()
                return
            checkpoint = self._build_checkpoint()
            if self._persistence is None:
                raise RuntimeError("persistent journal requires replay persistence")
            self._persistence.commits.commit(
                persistent_journal.pending_records,
                checkpoint,
            )
            persistent_journal.confirm_candle()
        except Exception:
            if persistent_journal is not None:
                persistent_journal.fail_candle()
            raise

    def _evaluate_candle(self, candle: ClosedCandle) -> bool:
        now = self._clock.advance_to_ms(candle.close_time_ms)
        before_intents = len(self._sink.intents)
        plan = EntryPlan(
            arbitration_key=self._request.context.arbitration_key,
            budget=BudgetSnapshot(
                requested=self._request.requested_amount,
                strategy_remaining=((self._request.strategy_id, self._request.strategy_remaining),),
                account_remaining=self._request.account_remaining,
                global_remaining=self._request.global_remaining,
            ),
            deadline_at=now + timedelta(seconds=self._request.timeframe_seconds),
            dispatch=False,
        )
        try:
            processed = self._core.process(
                CandleEnvelope.from_closed_candle(candle),
                (self._request.context,),
                (plan,),
                entitled_packs=self._request.entitled_packs,
            )
        except StrategyCatalogError as exc:
            self._remember(candle)
            ingress_record = self._record_accepted(candle, now)
            self._journal.append(
                DecisionEventType.STRATEGY_BLOCKED,
                occurred_at=now,
                correlation_id=candle.candle_id,
                causation_id=ingress_record.event.event_id,
                strategy_id=self._request.strategy_id,
                strategy_version=self._request.strategy_version,
                manifest_hash=self._request.manifest_hash,
                configuration_hash=self._request.configuration_hash,
                candle_id=candle.candle_id,
                payload=(("reason", exc.reason_code),),
            )
            return True
        if processed.ingress.status is not CandleIngressStatus.ACCEPTED:
            return False
        self._remember(candle)
        ingress_record = self._record_accepted(candle, now)
        result = processed.pipeline
        if result is None:
            return True
        for evaluation in result.evaluations:
            evaluation_record = self._journal.append(
                DecisionEventType.STRATEGY_EVALUATED,
                occurred_at=now,
                correlation_id=candle.candle_id,
                causation_id=ingress_record.event.event_id,
                strategy_id=self._request.strategy_id,
                strategy_version=self._request.strategy_version,
                manifest_hash=self._request.manifest_hash,
                configuration_hash=self._request.configuration_hash,
                candle_id=candle.candle_id,
                payload=(("reason", evaluation.reason.value),),
            )
            if evaluation.signal is not None:
                signal = evaluation.signal
                self._signal_ids.append(signal.signal_id)
                self._journal.append(
                    DecisionEventType.SIGNAL_CREATED,
                    occurred_at=now,
                    correlation_id=signal.correlation_id,
                    causation_id=evaluation_record.event.event_id,
                    strategy_id=self._request.strategy_id,
                    strategy_version=self._request.strategy_version,
                    manifest_hash=self._request.manifest_hash,
                    configuration_hash=self._request.configuration_hash,
                    candle_id=candle.candle_id,
                    payload=(
                        ("direction", signal.direction.value),
                        ("signal_id", signal.signal_id),
                    ),
                )
        for arbitration in result.arbitrations:
            self._arbitration_reasons.append(arbitration.reason.value)
            arbitrated = arbitration.arbitrated_signal
            self._journal.append(
                (
                    DecisionEventType.SIGNAL_CONSOLIDATED
                    if arbitrated is not None
                    else DecisionEventType.SIGNAL_CANCELLED
                ),
                occurred_at=now,
                correlation_id=(arbitrated.correlation_id if arbitrated else candle.candle_id),
                causation_id=ingress_record.event.event_id,
                strategy_id=self._request.strategy_id,
                strategy_version=self._request.strategy_version,
                manifest_hash=self._request.manifest_hash,
                configuration_hash=self._request.configuration_hash,
                candle_id=candle.candle_id,
                payload=(("reason", arbitration.reason.value),),
            )
        for allocation in result.allocations:
            self._allocation_reasons.append(allocation.reason.value)
            approved = allocation.allocation
            self._journal.append(
                (
                    DecisionEventType.ALLOCATION_APPROVED
                    if approved is not None
                    else DecisionEventType.ALLOCATION_REJECTED
                ),
                occurred_at=now,
                correlation_id=(
                    approved.arbitrated_signal.correlation_id if approved else candle.candle_id
                ),
                causation_id=ingress_record.event.event_id,
                strategy_id=self._request.strategy_id,
                strategy_version=self._request.strategy_version,
                manifest_hash=self._request.manifest_hash,
                configuration_hash=self._request.configuration_hash,
                candle_id=candle.candle_id,
                payload=(
                    ("amount_minor", str(approved.amount.minor_units) if approved else "0"),
                    ("reason", allocation.reason.value),
                ),
            )
        for replay_intent in self._sink.intents[before_intents:]:
            self._record_risk(replay_intent, candle, now, ingress_record)
        return True

    def process_many(self, candles: tuple[ClosedCandle, ...]) -> None:
        for candle in sorted(candles, key=lambda item: (item.close_time_ms, item.candle_id)):
            self.process(candle, dispatch=False)

    def checkpoint(self) -> WarmupCheckpoint:
        return self._build_checkpoint()

    def _build_checkpoint(self) -> WarmupCheckpoint:
        if not self._recent_ids:
            raise RuntimeError("cannot checkpoint an empty replay session")
        phase = (
            RuntimePhase.WARMING_UP
            if self._accepted_count < self._warmup_candles
            else RuntimePhase.ACTIVE
        )
        state = StrategyStateV1(tuple(self._recent_ids), self._accepted_count)
        return WarmupCheckpoint.create(
            self._request.context,
            manifest_sha256=self._request.manifest_hash,
            config_sha256=self._request.configuration_hash,
            runtime_phase=phase,
            state=state,
            last_close_time_ms=self._clock.now_ms(),
            created_at_ms=self._clock.now_ms(),
        )

    def result(self) -> ReplayResult:
        return ReplayResult(
            run_id=self._request.run_id,
            signal_ids=tuple(self._signal_ids),
            arbitration_reasons=tuple(self._arbitration_reasons),
            allocation_reasons=tuple(self._allocation_reasons),
            risk_decisions=tuple(self._risk_decisions),
            journal=self._journal.records,
            final_hash=self._journal.final_hash,
        )

    def complete(self) -> ReplayResult:
        if self._accepted_count != len(self._expected_ids):
            raise RuntimeError("replay cannot complete before every unique candle is processed")
        result = self.result()
        if self._persistence is not None:
            self._persistence.replays.append(ReplayRecord.completed(self._request, result))
        return result

    def _restore(
        self,
        checkpoint: WarmupCheckpoint,
        ingress_store: InMemoryCandleStore,
    ) -> None:
        self._validate_checkpoint_identity(checkpoint)
        if (
            self._journal.records
            and self._journal.records[-1].event.candle_id != checkpoint.last_candle_id
        ):
            raise CheckpointRestoreError(CheckpointRestoreReason.JOURNAL_DIVERGED)
        if self._persistence is None:
            raise CheckpointRestoreError(CheckpointRestoreReason.CANDLE_MISSING)
        restored: list[ClosedCandle] = []
        for candle_id in checkpoint.state.candle_ids:
            candle = self._persistence.candles.get(candle_id)
            if candle is None:
                raise CheckpointRestoreError(CheckpointRestoreReason.CANDLE_MISSING)
            restored.append(candle)
        if (
            restored[-1].candle_id != checkpoint.last_candle_id
            or restored[-1].close_time_ms != checkpoint.last_close_time_ms
        ):
            raise CheckpointRestoreError(CheckpointRestoreReason.CANDLE_MISSING)
        for candle in restored:
            outcome = ingress_store.append(candle)
            if outcome is not CandleStoreOutcome.STORED:
                raise CheckpointRestoreError(CheckpointRestoreReason.CANDLE_MISSING)
        try:
            self._runtimes.restore(
                self._request.context,
                tuple(market_candle_from_closed(candle) for candle in restored),
                candles_seen=checkpoint.candles_seen,
                entitled_packs=self._request.entitled_packs,
            )
        except StrategyCatalogError:
            raise
        except ValueError as exc:
            raise CheckpointRestoreError(CheckpointRestoreReason.CANDLE_MISSING) from exc
        self._recent_ids.extend(checkpoint.state.candle_ids)
        self._accepted_count = checkpoint.candles_seen

    def _validate_checkpoint_identity(self, checkpoint: WarmupCheckpoint) -> None:
        if checkpoint.compute_sha256() != checkpoint.checkpoint_sha256:
            raise CheckpointRestoreError(CheckpointRestoreReason.HASH_INVALID)
        if checkpoint.state.version != 1:
            raise CheckpointRestoreError(CheckpointRestoreReason.STATE_VERSION_UNSUPPORTED)
        if checkpoint.manifest_sha256 != self._request.manifest_hash:
            raise CheckpointRestoreError(CheckpointRestoreReason.MANIFEST_MISMATCH)
        if checkpoint.config_sha256 != self._request.configuration_hash:
            raise CheckpointRestoreError(CheckpointRestoreReason.CONFIG_MISMATCH)
        context = self._request.context
        checkpoint_context = (
            checkpoint.strategy_id,
            checkpoint.strategy_version,
            checkpoint.broker,
            checkpoint.account_id,
            checkpoint.product,
            checkpoint.symbol,
            checkpoint.timeframe_seconds,
            checkpoint.configuration_version,
        )
        expected_context = (
            context.strategy_id,
            context.strategy_version,
            context.broker.value,
            context.account_id,
            context.product,
            context.symbol,
            context.timeframe_seconds,
            context.configuration_version,
        )
        if checkpoint_context != expected_context:
            raise CheckpointRestoreError(CheckpointRestoreReason.CONTEXT_MISMATCH)

    def _hydrate_summary(self) -> set[str]:
        seen: set[str] = set()
        for record in self._journal.records:
            event = record.event
            payload = dict(event.payload)
            if event.event_type is DecisionEventType.SIGNAL_CREATED:
                self._signal_ids.append(payload["signal_id"])
            elif event.event_type in {
                DecisionEventType.SIGNAL_CANCELLED,
                DecisionEventType.SIGNAL_CONSOLIDATED,
            }:
                self._arbitration_reasons.append(payload["reason"])
            elif event.event_type in {
                DecisionEventType.ALLOCATION_APPROVED,
                DecisionEventType.ALLOCATION_REJECTED,
            }:
                self._allocation_reasons.append(payload["reason"])
            elif event.event_type is DecisionEventType.RISK_ACCEPTED:
                seen.add(event.correlation_id)
                self._risk_decisions.append(
                    ReplayRiskDecision(
                        correlation_id=event.correlation_id,
                        amount=self._request.requested_amount,
                        intent_id=_deterministic_id(
                            self._request.run_id, "intent", event.correlation_id
                        ),
                        reservation_id=_deterministic_id(
                            self._request.run_id, "reservation", event.correlation_id
                        ),
                        order_id=_deterministic_id(
                            self._request.run_id, "order", event.correlation_id
                        ),
                    )
                )
        return seen

    def _remember(self, candle: ClosedCandle) -> None:
        self._accepted_count += 1
        self._recent_ids.append(candle.candle_id)

    def _record_accepted(self, candle: ClosedCandle, now: datetime) -> DecisionRecord:
        return self._journal.append(
            DecisionEventType.CANDLE_ACCEPTED,
            occurred_at=now,
            correlation_id=candle.candle_id,
            causation_id=None,
            strategy_id=self._request.strategy_id,
            strategy_version=self._request.strategy_version,
            manifest_hash=self._request.manifest_hash,
            configuration_hash=self._request.configuration_hash,
            candle_id=candle.candle_id,
            payload=(),
        )

    def _record_risk(
        self,
        replay_intent: _ReplayIntent,
        candle: ClosedCandle,
        now: datetime,
        ingress_record: DecisionRecord,
    ) -> None:
        persisted = replay_intent.persisted
        risk = ReplayRiskDecision(
            correlation_id=replay_intent.request.correlation_id,
            amount=replay_intent.request.amount,
            intent_id=persisted.intent_id,
            reservation_id=persisted.reservation_id,
            order_id=persisted.order_id,
        )
        self._risk_decisions.append(risk)
        risk_record = self._journal.append(
            DecisionEventType.RISK_ACCEPTED,
            occurred_at=now,
            correlation_id=risk.correlation_id,
            causation_id=ingress_record.event.event_id,
            strategy_id=self._request.strategy_id,
            strategy_version=self._request.strategy_version,
            manifest_hash=self._request.manifest_hash,
            configuration_hash=self._request.configuration_hash,
            candle_id=candle.candle_id,
            payload=(
                ("amount_minor", str(risk.amount.minor_units)),
                ("currency", risk.amount.currency),
                ("reservation_id", risk.reservation_id),
            ),
        )
        self._journal.append(
            DecisionEventType.ORDER_INTENT_CREATED,
            occurred_at=now,
            correlation_id=risk.correlation_id,
            causation_id=risk_record.event.event_id,
            strategy_id=self._request.strategy_id,
            strategy_version=self._request.strategy_version,
            manifest_hash=self._request.manifest_hash,
            configuration_hash=self._request.configuration_hash,
            candle_id=candle.candle_id,
            payload=(("intent_id", risk.intent_id), ("order_id", risk.order_id)),
        )


class ReplayEngine:
    def __init__(
        self,
        catalog_factory: Callable[[], StrategyCatalog],
        *,
        max_candles: int = 100_000,
        max_journal_events: int = 1_000_000,
    ) -> None:
        if max_candles <= 0 or max_journal_events <= 0:
            raise ValueError("replay capacities must be positive")
        self._catalog_factory = catalog_factory
        self._max_candles = max_candles
        self._max_journal_events = max_journal_events

    def create_session(
        self,
        request: ReplayRequest,
        *,
        persistence: ReplayPersistence | None = None,
        checkpoint: WarmupCheckpoint | None = None,
    ) -> ReplaySession:
        if len(request.candles) > self._max_candles:
            raise ValueError("replay candle capacity exceeded")
        catalog = self._catalog_factory()
        entry = catalog.get(request.strategy_id, request.strategy_version)
        actual_manifest_hash = hashlib.sha256(entry.manifest.canonical_bytes()).hexdigest()
        if actual_manifest_hash != request.manifest_hash:
            raise ValueError("replay manifest hash mismatch")
        return ReplaySession(
            request,
            catalog,
            max_candles=self._max_candles,
            max_journal_events=self._max_journal_events,
            persistence=persistence,
            checkpoint=checkpoint,
        )

    def run(
        self,
        request: ReplayRequest,
        *,
        persistence: ReplayPersistence | None = None,
    ) -> ReplayResult:
        if persistence is not None:
            existing = persistence.replays.get(request.run_id)
            if existing is not None:
                restored = self.create_session(request, persistence=persistence).result()
                if ReplayRecord.completed(request, restored) != existing:
                    raise ReplayConflictError("persisted replay proof does not match its journal")
                return restored
        session = self.create_session(request, persistence=persistence)
        session.process_many(request.candles)
        return session.complete()
