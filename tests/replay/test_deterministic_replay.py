from __future__ import annotations

import hashlib
import time

import pytest

from packages.audit import DecisionEventType, verify_decision_chain
from packages.domain.models import Broker, Direction, Money
from packages.market_data import ClosedCandle
from packages.replay import ReplayEngine, ReplayRequest, configuration_hash_for
from packages.strategy_catalog import ReleaseStatus, StrategyCatalog, ValidationRegistry
from tests.helpers.strategy_fixtures import register_released

STRATEGY_ID = "replay-fixed-call"


def make_catalog(*, suspended: bool = False) -> StrategyCatalog:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    manifest = register_released(catalog, registry, STRATEGY_ID, Direction.CALL)
    if suspended:
        catalog.transition(manifest.strategy_id, manifest.version, ReleaseStatus.SUSPENDED)
    return catalog


def closed_candle(index: int, *, source_event_id: str | None = None) -> ClosedCandle:
    open_time_ms = 1_700_000_000_000 + index * 60_000
    close_units = 101_000 if index % 2 == 0 else 99_000
    return ClosedCandle(
        broker=Broker.DERIV,
        symbol="EURUSD",
        timeframe_seconds=60,
        open_time_ms=open_time_ms,
        close_time_ms=open_time_ms + 60_000,
        open_units=100_000,
        high_units=102_000,
        low_units=98_000,
        close_units=close_units,
        price_scale=1_000,
        source="FAKE_REPLAY_SOURCE",
        source_event_id=source_event_id or f"fake-{index}",
        source_timestamp_ms=open_time_ms + 60_000,
        received_timestamp_ms=open_time_ms + 60_005,
    )


def request_for(
    candles: tuple[ClosedCandle, ...],
    *,
    suspended: bool = False,
) -> ReplayRequest:
    catalog = make_catalog(suspended=suspended)
    manifest = catalog.get(STRATEGY_ID, "1.0.0").manifest
    config_hash = configuration_hash_for("config-replay-1", ())
    return ReplayRequest(
        strategy_id=STRATEGY_ID,
        strategy_version="1.0.0",
        broker=Broker.DERIV,
        account_id="demo-replay-account",
        product="DIGITAL_OPTION",
        symbol="EURUSD",
        timeframe_seconds=60,
        configuration_version="config-replay-1",
        parameters=(),
        configuration_hash=config_hash,
        manifest_hash=hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
        entitled_packs=frozenset({"phase0-candidates"}),
        requested_amount=Money(100, "USD"),
        strategy_remaining=Money(100, "USD"),
        account_remaining=Money(100, "USD"),
        global_remaining=Money(100, "USD"),
        candles=candles,
    )


def test_replay_is_identical_twice_and_after_complete_engine_recreation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candles = tuple(closed_candle(index) for index in range(500))
    request = request_for(tuple(reversed(candles)))

    def wall_clock_forbidden() -> float:
        raise AssertionError("replay accessed wall clock")

    monkeypatch.setattr(time, "time", wall_clock_forbidden)
    engine = ReplayEngine(make_catalog)
    first = engine.run(request)
    second = engine.run(request)
    recreated = ReplayEngine(make_catalog).run(request)

    assert first == second == recreated
    assert len(first.signal_ids) == 500
    assert len(first.risk_decisions) == 500
    assert verify_decision_chain(first.journal)
    assert first.final_hash == recreated.final_hash
    assert {record.event.manifest_hash for record in first.journal} == {request.manifest_hash}
    assert {record.event.configuration_hash for record in first.journal} == {
        request.configuration_hash
    }


def test_duplicate_candle_reaches_runtime_and_risk_exactly_once() -> None:
    first_delivery = closed_candle(0, source_event_id="delivery-a")
    duplicate_delivery = closed_candle(0, source_event_id="delivery-b")
    result = ReplayEngine(make_catalog).run(request_for((first_delivery, duplicate_delivery)))

    assert len(result.signal_ids) == 1
    assert len(result.risk_decisions) == 1
    assert (
        sum(
            record.event.event_type is DecisionEventType.CANDLE_ACCEPTED
            for record in result.journal
        )
        == 1
    )


def test_replay_sorts_input_before_evaluation() -> None:
    ordered = tuple(closed_candle(index) for index in range(5))
    forward = ReplayEngine(make_catalog).run(request_for(ordered))
    reversed_result = ReplayEngine(make_catalog).run(request_for(tuple(reversed(ordered))))

    assert forward.signal_ids == reversed_result.signal_ids
    assert forward.risk_decisions == reversed_result.risk_decisions
    assert forward.final_hash == reversed_result.final_hash


def test_suspended_strategy_is_journaled_and_creates_no_new_intent() -> None:
    request = request_for((closed_candle(0), closed_candle(1)), suspended=True)
    result = ReplayEngine(lambda: make_catalog(suspended=True)).run(request)

    assert result.signal_ids == ()
    assert result.risk_decisions == ()
    blocked = [
        record
        for record in result.journal
        if record.event.event_type is DecisionEventType.STRATEGY_BLOCKED
    ]
    assert len(blocked) == 2
    assert {dict(record.event.payload)["reason"] for record in blocked} == {"HG_STRATEGY_SUSPENDED"}
    assert verify_decision_chain(result.journal)
