from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from packages.domain.models import Broker, Direction, Money
from packages.portfolio_allocation import AllocationReason, BudgetSnapshot, PortfolioAllocator
from packages.signal_arbitration import ArbitrationReason, SignalArbiter
from packages.strategies import StrategyEvaluationReason, StrategyRuntimeManager
from packages.strategy_catalog import (
    DataRequirement,
    ReleaseStatus,
    StrategyCatalog,
    StrategyCatalogError,
    StrategyCatalogReason,
    StrategyManifest,
    ValidationRegistry,
)
from tests.helpers.strategy_fixtures import (
    FixedDirectionStrategy,
    artifact_for,
    candle_for,
    context_for,
    manifest_for,
    record_release_evidence,
    register_released,
)

ENTITLED = frozenset({"phase0-candidates"})


def test_manifest_external_roundtrip_is_strict_and_immutable() -> None:
    artifact = artifact_for("manifest-test", Direction.CALL)
    manifest = manifest_for("manifest-test", artifact)
    restored = StrategyManifest.from_external_payload(manifest.to_payload())
    assert restored == manifest
    assert restored.canonical_bytes() == manifest.canonical_bytes()

    malformed = manifest.to_payload()
    malformed["unexpected"] = True
    with pytest.raises(ValueError, match="schema"):
        StrategyManifest.from_external_payload(malformed)
    with pytest.raises(ValueError, match="SHA-256"):
        replace(manifest, code_hash="not-a-hash")


def test_catalog_rejects_hash_mismatch_and_release_without_all_evidence() -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    artifact = artifact_for("hash-test", Direction.CALL)
    manifest = manifest_for("hash-test", artifact)
    with pytest.raises(StrategyCatalogError) as implementation_mismatch:
        catalog.register(
            manifest,
            FixedDirectionStrategy(b"different-packaged-code", Direction.CALL),
            artifact,
        )
    assert implementation_mismatch.value.reason is StrategyCatalogReason.HASH_MISMATCH
    with pytest.raises(StrategyCatalogError) as incomplete:
        catalog.register(
            manifest,
            FixedDirectionStrategy(artifact, Direction.CALL),
            artifact,
        )
    assert incomplete.value.reason is StrategyCatalogReason.VALIDATION_INCOMPLETE

    record_release_evidence(registry, manifest)
    with pytest.raises(StrategyCatalogError) as altered:
        catalog.register(
            manifest,
            FixedDirectionStrategy(artifact + b"altered", Direction.CALL),
            artifact + b"altered",
        )
    assert altered.value.reason is StrategyCatalogReason.HASH_MISMATCH


def test_lifecycle_requires_ordered_promotion_and_validation_before_release() -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    artifact = artifact_for("lifecycle-test", Direction.CALL)
    draft = manifest_for("lifecycle-test", artifact, status=ReleaseStatus.DRAFT)
    catalog.register(draft, FixedDirectionStrategy(artifact, Direction.CALL), artifact)

    with pytest.raises(StrategyCatalogError) as skipped:
        catalog.transition(draft.strategy_id, draft.version, ReleaseStatus.RELEASED)
    assert skipped.value.reason is StrategyCatalogReason.LIFECYCLE_INVALID
    for status in (
        ReleaseStatus.BACKTESTED,
        ReleaseStatus.WALK_FORWARD_VALIDATED,
        ReleaseStatus.REPLAY_VALIDATED,
        ReleaseStatus.PRACTICE_VALIDATED,
    ):
        catalog.transition(draft.strategy_id, draft.version, status)
    with pytest.raises(StrategyCatalogError) as no_evidence:
        catalog.transition(draft.strategy_id, draft.version, ReleaseStatus.RELEASED)
    assert no_evidence.value.reason is StrategyCatalogReason.VALIDATION_INCOMPLETE

    record_release_evidence(registry, draft)
    released = catalog.transition(draft.strategy_id, draft.version, ReleaseStatus.RELEASED)
    assert released.manifest.release_status is ReleaseStatus.RELEASED


def test_catalog_compatibility_entitlement_and_suspension_fail_closed() -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    manifest = register_released(catalog, registry, "eligibility-test", Direction.CALL)
    context = context_for(manifest.strategy_id)

    with pytest.raises(StrategyCatalogError) as missing:
        catalog.activate(
            context,
            entitled_packs=frozenset(),
            available_data=frozenset({DataRequirement.CLOSED_CANDLES}),
        )
    assert missing.value.reason is StrategyCatalogReason.ENTITLEMENT_MISSING

    incompatible = replace(context, timeframe_seconds=300)
    with pytest.raises(StrategyCatalogError) as mismatch:
        catalog.activate(
            incompatible,
            entitled_packs=ENTITLED,
            available_data=frozenset({DataRequirement.CLOSED_CANDLES}),
        )
    assert mismatch.value.reason is StrategyCatalogReason.INCOMPATIBLE

    catalog.transition(manifest.strategy_id, manifest.version, ReleaseStatus.SUSPENDED)
    with pytest.raises(StrategyCatalogError) as suspended:
        catalog.activate(
            context,
            entitled_packs=ENTITLED,
            available_data=frozenset({DataRequirement.CLOSED_CANDLES}),
        )
    assert suspended.value.reason is StrategyCatalogReason.SUSPENDED


def test_runtime_uses_only_closed_ordered_candles_and_isolates_every_context() -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    manifest = register_released(
        catalog,
        registry,
        "runtime-test",
        Direction.CALL,
        warmup_candles=2,
    )
    manager = StrategyRuntimeManager(catalog)
    first_context = context_for(manifest.strategy_id)
    second_context = context_for(manifest.strategy_id, account_id="demo-account-2")
    first_close = datetime(2026, 8, 20, 12, 1, tzinfo=UTC)

    open_result = manager.evaluate(
        first_context,
        candle_for(first_close, closed=False),
        entitled_packs=ENTITLED,
    )
    assert open_result.reason is StrategyEvaluationReason.CANDLE_NOT_CLOSED
    warmup = manager.evaluate(
        first_context,
        candle_for(first_close),
        entitled_packs=ENTITLED,
    )
    assert warmup.reason is StrategyEvaluationReason.WARMING_UP
    duplicate = manager.evaluate(
        first_context,
        candle_for(first_close),
        entitled_packs=ENTITLED,
    )
    assert duplicate.reason is StrategyEvaluationReason.DUPLICATE_CANDLE
    out_of_order = manager.evaluate(
        first_context,
        candle_for(first_close - timedelta(minutes=1)),
        entitled_packs=ENTITLED,
    )
    assert out_of_order.reason is StrategyEvaluationReason.OUT_OF_ORDER_CANDLE
    signal = manager.evaluate(
        first_context,
        candle_for(first_close + timedelta(minutes=1)),
        entitled_packs=ENTITLED,
    )
    assert signal.reason is StrategyEvaluationReason.SIGNAL

    other_warmup = manager.evaluate(
        second_context,
        candle_for(first_close),
        entitled_packs=ENTITLED,
    )
    assert other_warmup.reason is StrategyEvaluationReason.WARMING_UP
    assert manager.instance_count == 2


def test_runtime_replay_is_deterministic_for_same_version_and_context() -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    manifest = register_released(catalog, registry, "replay-test", Direction.PUT)
    context = context_for(manifest.strategy_id)
    candle = candle_for(datetime(2026, 8, 20, 12, 1, tzinfo=UTC), rising=False)
    first = StrategyRuntimeManager(catalog).evaluate(context, candle, entitled_packs=ENTITLED)
    second = StrategyRuntimeManager(catalog).evaluate(context, candle, entitled_packs=ENTITLED)
    assert first.signal == second.signal


def test_arbiter_cancels_opposites_and_deduplicates_equal_signals_without_stake() -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    register_released(catalog, registry, "arbiter-call-a", Direction.CALL)
    register_released(catalog, registry, "arbiter-call-b", Direction.CALL)
    register_released(catalog, registry, "arbiter-put", Direction.PUT)
    manager = StrategyRuntimeManager(catalog)
    close = datetime(2026, 8, 20, 12, 1, tzinfo=UTC)

    signals = []
    for strategy_id in ("arbiter-call-a", "arbiter-call-b", "arbiter-put"):
        evaluation = manager.evaluate(
            context_for(strategy_id), candle_for(close), entitled_packs=ENTITLED
        )
        assert evaluation.signal is not None
        signals.append(evaluation.signal)
    arbiter = SignalArbiter(catalog)
    opposite = arbiter.arbitrate_all(tuple(signals), now=close)
    assert opposite[0].reason is ArbitrationReason.OPPOSING_SIGNALS_CANCELLED
    assert opposite[0].arbitrated_signal is None

    consensus = arbiter.arbitrate_all(tuple(signals[:2] + [signals[0]]), now=close)
    assert consensus[0].reason is ArbitrationReason.CONSENSUS_NO_STAKE_SUM
    assert consensus[0].arbitrated_signal is not None
    assert len(consensus[0].arbitrated_signal.source_signal_ids) == 2


def test_arbiter_rechecks_expiry_and_catalog_suspension() -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    manifest = register_released(catalog, registry, "arbiter-status", Direction.CALL)
    close = datetime(2026, 8, 20, 12, 1, tzinfo=UTC)
    evaluation = StrategyRuntimeManager(catalog).evaluate(
        context_for(manifest.strategy_id), candle_for(close), entitled_packs=ENTITLED
    )
    assert evaluation.signal is not None
    arbiter = SignalArbiter(catalog)
    expired = arbiter.arbitrate_all((evaluation.signal,), now=close + timedelta(minutes=1))
    assert expired[0].reason is ArbitrationReason.ALL_SIGNALS_EXPIRED

    catalog.transition(manifest.strategy_id, manifest.version, ReleaseStatus.SUSPENDED)
    suspended = arbiter.arbitrate_all((evaluation.signal,), now=close)
    assert suspended[0].reason is ArbitrationReason.ALL_STRATEGIES_INELIGIBLE


def test_same_strategy_version_in_two_configurations_still_cannot_multiply_stake() -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    manifest = register_released(catalog, registry, "multi-config", Direction.CALL)
    runtime = StrategyRuntimeManager(catalog)
    close = datetime(2026, 8, 20, 12, 1, tzinfo=UTC)
    first = runtime.evaluate(
        context_for(manifest.strategy_id, configuration_version="config-1"),
        candle_for(close),
        entitled_packs=ENTITLED,
    ).signal
    second = runtime.evaluate(
        context_for(manifest.strategy_id, configuration_version="config-2"),
        candle_for(close),
        entitled_packs=ENTITLED,
    ).signal
    assert first is not None and second is not None
    decision = SignalArbiter(catalog).arbitrate_all((first, second), now=close)[0]
    assert decision.reason is ArbitrationReason.CONSENSUS_NO_STAKE_SUM
    assert decision.arbitrated_signal is not None
    assert decision.arbitrated_signal.source_strategy_keys == (("multi-config", "1.0.0"),)


def test_allocator_applies_strategy_account_and_global_budget_without_summing() -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    register_released(catalog, registry, "allocation-a", Direction.CALL)
    register_released(catalog, registry, "allocation-b", Direction.CALL)
    close = datetime(2026, 8, 20, 12, 1, tzinfo=UTC)
    runtime = StrategyRuntimeManager(catalog)
    signals = tuple(
        runtime.evaluate(
            context_for(strategy_id), candle_for(close), entitled_packs=ENTITLED
        ).signal
        for strategy_id in ("allocation-a", "allocation-b")
    )
    assert all(signal is not None for signal in signals)
    arbitrated = SignalArbiter(catalog).arbitrate_all(signals, now=close)[0].arbitrated_signal
    assert arbitrated is not None
    allocator = PortfolioAllocator()
    budget = BudgetSnapshot(
        requested=Money(1_000, "USD"),
        strategy_remaining=(
            ("allocation-a", Money(1_000, "USD")),
            ("allocation-b", Money(1_000, "USD")),
        ),
        account_remaining=Money(1_000, "USD"),
        global_remaining=Money(1_000, "USD"),
    )
    approved = allocator.allocate(arbitrated, budget)
    assert approved.reason is AllocationReason.APPROVED
    assert approved.allocation is not None
    assert approved.allocation.amount.minor_units == 1_000

    exceeded = allocator.allocate(
        arbitrated,
        replace(budget, global_remaining=Money(999, "USD")),
    )
    assert exceeded.reason is AllocationReason.BUDGET_EXCEEDED
    mismatch = allocator.allocate(
        arbitrated,
        replace(budget, account_remaining=Money(1_000, "EUR")),
    )
    assert mismatch.reason is AllocationReason.CURRENCY_MISMATCH


def test_different_broker_or_account_contexts_are_arbitrated_separately() -> None:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    register_released(catalog, registry, "context-a", Direction.CALL)
    register_released(catalog, registry, "context-b", Direction.PUT)
    close = datetime(2026, 8, 20, 12, 1, tzinfo=UTC)
    runtime = StrategyRuntimeManager(catalog)
    first = runtime.evaluate(
        context_for("context-a"), candle_for(close), entitled_packs=ENTITLED
    ).signal
    second = runtime.evaluate(
        context_for("context-b", account_id="demo-account-2"),
        candle_for(close),
        entitled_packs=ENTITLED,
    ).signal
    assert first is not None and second is not None
    decisions = SignalArbiter(catalog).arbitrate_all((first, second), now=close)
    assert len(decisions) == 2
    assert all(decision.reason is ArbitrationReason.SINGLE_SIGNAL for decision in decisions)
    assert {decision.arbitration_key[0] for decision in decisions} == {Broker.DERIV}
