from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from packages.domain.models import Broker, Direction
from packages.signal_arbitration.arbiter import SignalArbiter
from packages.signal_arbitration.models import ArbitrationReason
from packages.strategies.models import RuntimeContext, StrategySignal
from packages.strategy_catalog import StrategyCatalog, ValidationRegistry
from tests.helpers.strategy_fixtures import (
    register_released,
)


def _build_catalog() -> StrategyCatalog:
    registry = ValidationRegistry()
    catalog = StrategyCatalog(registry)
    register_released(catalog, registry, "strat_trend", Direction.CALL)
    return catalog


def _make_signal(
    strategy_id: str,
    broker: Broker,
    account_id: str,
    symbol: str,
    direction: Direction,
    now: datetime,
) -> StrategySignal:
    context = RuntimeContext(
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        broker=broker,
        account_id=account_id,
        product="BINARY_OPTION",
        symbol=symbol,
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )
    return StrategySignal(
        signal_id=str(uuid4()),
        correlation_id=str(uuid4()),
        context=context,
        direction=direction,
        created_at=now,
        valid_until=now + timedelta(seconds=30),
        candle_close_time=now,
        evidence=(("sample", "1"),),
    )


def test_cross_broker_arbiter_opposing_signals_cancelled() -> None:
    catalog = _build_catalog()
    arbiter = SignalArbiter(catalog)
    now = datetime.now(UTC)

    # 1. Deriv emits CALL on frxEURUSD
    sig_deriv = _make_signal(
        "strat_trend", Broker.DERIV, "VRTC1001", "frxEURUSD", Direction.CALL, now
    )
    # 2. IQ Option emits PUT on EURUSD for the same timeframe
    sig_iq = _make_signal(
        "strat_trend", Broker.IQ_OPTION, "PRACTICE_01", "EURUSD", Direction.PUT, now
    )

    decisions = arbiter.arbitrate_cross_broker((sig_deriv, sig_iq), now=now)
    assert len(decisions) == 1
    decision = decisions[0]

    assert decision.reason is ArbitrationReason.OPPOSING_SIGNALS_CANCELLED
    assert decision.arbitrated_signal is None
    assert set(decision.considered_signal_ids) == {sig_deriv.signal_id, sig_iq.signal_id}


def test_cross_broker_arbiter_consensus_no_stake_sum() -> None:
    catalog = _build_catalog()
    arbiter = SignalArbiter(catalog)
    now = datetime.now(UTC)

    # Both Deriv and IQ Option emit CALL on EURUSD
    sig_deriv = _make_signal(
        "strat_trend", Broker.DERIV, "VRTC1001", "frxEURUSD", Direction.CALL, now
    )
    sig_iq = _make_signal(
        "strat_trend", Broker.IQ_OPTION, "PRACTICE_01", "EURUSD", Direction.CALL, now
    )

    decisions = arbiter.arbitrate_cross_broker((sig_deriv, sig_iq), now=now)
    assert len(decisions) == 1
    decision = decisions[0]

    assert decision.reason is ArbitrationReason.CONSENSUS_NO_STAKE_SUM
    assert decision.arbitrated_signal is not None
    assert decision.arbitrated_signal.direction is Direction.CALL
    assert set(decision.arbitrated_signal.source_signal_ids) == {
        sig_deriv.signal_id,
        sig_iq.signal_id,
    }


def test_cross_broker_arbiter_independent_symbols() -> None:
    catalog = _build_catalog()
    arbiter = SignalArbiter(catalog)
    now = datetime.now(UTC)

    sig_eurusd = _make_signal(
        "strat_trend", Broker.DERIV, "VRTC1001", "frxEURUSD", Direction.CALL, now
    )
    sig_gbpusd = _make_signal(
        "strat_trend", Broker.IQ_OPTION, "PRACTICE_01", "GBPUSD", Direction.PUT, now
    )

    decisions = arbiter.arbitrate_cross_broker((sig_eurusd, sig_gbpusd), now=now)
    assert len(decisions) == 2
    reasons = {d.reason for d in decisions}
    assert reasons == {ArbitrationReason.SINGLE_SIGNAL}
