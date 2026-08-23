from __future__ import annotations

import pytest

from packages.domain.models import Broker
from packages.market_pipeline import (
    MarketHealthGate,
    MarketHealthReason,
    MarketSeriesHealth,
    MarketSeriesId,
)


def series(symbol: str = "R_100") -> MarketSeriesId:
    return MarketSeriesId(Broker.DERIV, symbol, symbol, "DIGITAL_OPTION", 60)


def healthy_gate() -> tuple[MarketHealthGate, MarketSeriesId]:
    gate = MarketHealthGate()
    identity = series()
    gate.register(identity, required_closed_candles=2)
    assert gate.complete_recovery(
        identity,
        generation=0,
        continuity_valid=True,
        clock_trusted=True,
        durable_closed_candles=2,
        last_durable_close=120_000,
        last_source_event="history-1",
    )
    return gate, identity


def test_healthy_allows_shadow_delivery_and_broker_health_is_derived() -> None:
    gate, identity = healthy_gate()
    other = series("R_50")
    gate.register(other, required_closed_candles=1)
    gate.complete_recovery(
        other,
        generation=0,
        continuity_valid=True,
        clock_trusted=True,
        durable_closed_candles=1,
        last_durable_close=60_000,
        last_source_event="history-2",
    )
    assert gate.snapshot(identity).dispatch_allowed
    aggregate = gate.broker_snapshot(Broker.DERIV)
    assert aggregate.health is MarketSeriesHealth.HEALTHY
    assert aggregate.blocked_series == 0


@pytest.mark.parametrize(
    ("transition", "expected"),
    (
        ("gap", MarketSeriesHealth.GAPPED),
        ("backpressure", MarketSeriesHealth.BACKPRESSURED),
        ("stale", MarketSeriesHealth.STALE),
        ("reconnect", MarketSeriesHealth.RECONNECTING),
        ("clock", MarketSeriesHealth.CLOCK_UNTRUSTED),
        ("failed", MarketSeriesHealth.FAILED),
        ("incompatible", MarketSeriesHealth.INCOMPATIBLE),
    ),
)
def test_unhealthy_states_block_shadow_delivery(
    transition: str, expected: MarketSeriesHealth
) -> None:
    gate, identity = healthy_gate()
    if transition == "gap":
        gate.mark_gap(identity)
    elif transition == "backpressure":
        gate.mark_backpressure(identity)
    elif transition == "stale":
        gate.mark_suspended(identity)
    elif transition == "reconnect":
        gate.start_reconnect(identity)
    elif transition == "clock":
        gate.mark_clock_untrusted(identity)
    elif transition == "failed":
        gate.mark_failed(identity)
    else:
        gate.mark_incompatible(identity)
    snapshot = gate.snapshot(identity)
    assert snapshot.health is expected
    assert not snapshot.dispatch_allowed


def test_warming_up_blocks_decisions_and_queue_drain_does_not_clear_backpressure() -> None:
    gate = MarketHealthGate()
    identity = series()
    gate.register(identity, required_closed_candles=3)
    gate.mark_warming_up(
        identity,
        durable_closed_candles=2,
        last_durable_close=120_000,
        last_source_event="warmup",
    )
    assert gate.snapshot(identity).health is MarketSeriesHealth.WARMING_UP
    assert not gate.snapshot(identity).dispatch_allowed

    gate.mark_backpressure(identity)
    assert gate.snapshot(identity).backpressure_active
    assert not gate.snapshot(identity).dispatch_allowed
    assert gate.complete_recovery(
        identity,
        generation=0,
        continuity_valid=True,
        clock_trusted=True,
        durable_closed_candles=3,
        last_durable_close=180_000,
        last_source_event="backfill",
    )
    assert gate.snapshot(identity).health is MarketSeriesHealth.HEALTHY
    assert not gate.snapshot(identity).backpressure_active


def test_stale_reconnect_generation_cannot_reopen_gate() -> None:
    gate, identity = healthy_gate()
    generation = gate.start_reconnect(identity)
    newer = gate.start_reconnect(identity)
    assert newer == generation + 1
    assert not gate.complete_recovery(
        identity,
        generation=generation,
        continuity_valid=True,
        clock_trusted=True,
        durable_closed_candles=3,
        last_durable_close=180_000,
        last_source_event="stale-response",
    )
    snapshot = gate.snapshot(identity)
    assert snapshot.health is MarketSeriesHealth.RECONNECTING
    assert snapshot.reason is MarketHealthReason.RECONNECT_REQUIRED
