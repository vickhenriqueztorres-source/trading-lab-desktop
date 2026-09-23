"""Comprehensive unit tests for Hack Chino 4 Scenarios + 5 Quant Models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies.iqoption_hack_chino import (
    IQOPTION_HACK_CHINO_STRATEGY_ID,
    AnalogousSituationsKnnModel,
    BollingerExhaustionTrigger,
    BreakoutFlowTrigger,
    ContextBayesianModel,
    EmaPullbackEngulfTrigger,
    IQOptionHackChinoStrategy,
    LogisticFactorModel,
    ModelEstimate,
    ProbabilisticRegimeModel,
    RejectionWickTrigger,
    SequenceMarkovModel,
    TriggerSignal,
    iqoption_hack_chino_manifest,
    qualifies_for_martingale,
)
from packages.strategies.models import RuntimeContext

NOW = datetime(2026, 9, 22, 14, 0, 0, tzinfo=UTC)


def make_candle(
    open_price: str,
    high_price: str,
    low_price: str,
    close_price: str,
    minute_offset: int = 0,
    symbol: str = "EURUSD",
) -> MarketCandle:
    ot = NOW + timedelta(minutes=minute_offset)
    return MarketCandle(
        broker=Broker.IQ_OPTION,
        broker_symbol=symbol,
        timeframe_seconds=60,
        open_time=ot,
        close_time=ot + timedelta(seconds=60),
        open=Decimal(open_price),
        high=Decimal(high_price),
        low=Decimal(low_price),
        close=Decimal(close_price),
        is_closed=True,
    )


def generate_candle_series(count: int, trend: str = "flat") -> list[MarketCandle]:
    candles: list[MarketCandle] = []
    price = Decimal("1.1000")
    for i in range(count):
        if trend == "bull":
            open_p = price
            close_p = price + Decimal("0.0005")
            high_p = close_p + Decimal("0.0002")
            low_p = open_p - Decimal("0.0001")
            price = close_p
        elif trend == "bear":
            open_p = price
            close_p = price - Decimal("0.0005")
            high_p = open_p + Decimal("0.0001")
            low_p = close_p - Decimal("0.0002")
            price = close_p
        else:
            delta = Decimal("0.0003") if i % 2 == 0 else Decimal("-0.0003")
            open_p = price
            close_p = price + delta
            high_p = max(open_p, close_p) + Decimal("0.0002")
            low_p = min(open_p, close_p) - Decimal("0.0002")
            price = close_p
        candles.append(
            make_candle(
                str(open_p),
                str(high_p),
                str(low_p),
                str(close_p),
                minute_offset=i - count,
            )
        )
    return candles


def test_hack_chino_manifest() -> None:
    manifest = iqoption_hack_chino_manifest()
    assert manifest.strategy_id == IQOPTION_HACK_CHINO_STRATEGY_ID
    assert manifest.version == "1.0.0"
    assert manifest.warmup_candles == 45
    assert manifest.supported_timeframes == (60,)


def test_context_bayesian_model() -> None:
    candles = generate_candle_series(50, trend="bull")
    est = ContextBayesianModel.estimate(candles)
    assert est.model_name == "Contexto Bayesiano"
    assert 0.0 <= est.prob_call <= 1.0
    assert 0.0 <= est.prob_put <= 1.0


def test_sequence_markov_model() -> None:
    candles = generate_candle_series(50, trend="bull")
    est = SequenceMarkovModel.estimate(candles)
    assert est.model_name == "Sequências Markov"
    assert 0.0 <= est.prob_call <= 1.0
    assert 0.0 <= est.prob_put <= 1.0


def test_logistic_factor_model() -> None:
    candles = generate_candle_series(50, trend="bear")
    est = LogisticFactorModel.estimate(candles)
    assert est.model_name == "Regressão Logística"
    assert 0.0 <= est.prob_call <= 1.0
    assert 0.0 <= est.prob_put <= 1.0


def test_analogous_situations_knn_model() -> None:
    candles = generate_candle_series(50, trend="flat")
    est = AnalogousSituationsKnnModel.estimate(candles)
    assert est.model_name == "Analogias (k-NN)"
    assert 0.0 <= est.prob_call <= 1.0
    assert 0.0 <= est.prob_put <= 1.0


def test_probabilistic_regime_model() -> None:
    candles = generate_candle_series(50, trend="bull")
    est = ProbabilisticRegimeModel.estimate(candles)
    assert est.model_name == "Regimes Probabilísticos"
    assert 0.0 <= est.prob_call <= 1.0
    assert 0.0 <= est.prob_put <= 1.0


def test_hack_chino_directional_conflict_veto() -> None:
    strategy = IQOptionHackChinoStrategy()
    m_call = ModelEstimate("M1", 0.65, 0.35, Direction.CALL, 1.0, "call")
    m_put = ModelEstimate("M2", 0.35, 0.65, Direction.PUT, 1.0, "put")
    m_neutral = ModelEstimate("M3", 0.50, 0.50, None, 1.0, "neutral")

    ensemble = strategy.arbitrate_models((m_call, m_put, m_neutral))
    assert ensemble.has_conflict is True
    assert ensemble.direction is None
    assert ensemble.rejection_reason == "DIRECTIONAL_CONFLICT_VETO"

    # Martingale qualification operates under standard rules without extra external veto
    qualifies, reason = strategy.qualifies_for_martingale(1, ensemble)
    assert qualifies is True
    assert reason == "MARTINGALE_STANDARD"


def test_hack_chino_confluence_threshold() -> None:
    strategy = IQOptionHackChinoStrategy()
    m_call1 = ModelEstimate("M1", 0.60, 0.40, Direction.CALL, 1.0, "call")
    m_call2 = ModelEstimate("M2", 0.60, 0.40, Direction.CALL, 1.0, "call")
    m_neutral = ModelEstimate("M3", 0.53, 0.47, None, 1.0, "neutral")

    ensemble_g0 = strategy.arbitrate_models((m_call1, m_call2, m_neutral))
    assert ensemble_g0.direction is Direction.CALL
    assert ensemble_g0.ensemble_prob >= 0.565

    # Standard Martingale confirms qualification on next candle
    qualifies, reason = qualifies_for_martingale(1, ensemble_g0)
    assert qualifies is True
    assert reason == "MARTINGALE_STANDARD"


def test_rejection_wick_trigger() -> None:
    """Scenario 1: Institutional Rejection Wick (CALL and PUT)."""
    candles = generate_candle_series(25, trend="flat")

    # Construct CALL rejection: sweeps below recent lows and closes high with lower wick >= 35%
    prior_low = min(float(c.low) for c in candles[-8:])
    call_candle = make_candle(
        open_price=str(prior_low + 0.0005),
        high_price=str(prior_low + 0.0010),
        low_price=str(prior_low - 0.0008),  # deep sweep
        close_price=str(prior_low + 0.0008),  # strong rejection
        minute_offset=1,
    )
    sig_call = RejectionWickTrigger.evaluate(candles + [call_candle])
    assert sig_call is not None
    assert sig_call.direction is Direction.CALL
    assert "sweep_low" in sig_call.details

    # Construct PUT rejection: sweeps above recent highs and closes low with upper wick >= 35%
    prior_high = max(float(c.high) for c in candles[-8:])
    put_candle = make_candle(
        open_price=str(prior_high - 0.0005),
        high_price=str(prior_high + 0.0008),  # sweep high
        low_price=str(prior_high - 0.0010),
        close_price=str(prior_high - 0.0008),  # strong rejection
        minute_offset=1,
    )
    sig_put = RejectionWickTrigger.evaluate(candles + [put_candle])
    assert sig_put is not None
    assert sig_put.direction is Direction.PUT
    assert "sweep_high" in sig_put.details


def test_breakout_flow_trigger() -> None:
    """Scenario 2: Micro-Range Breakout with Momentum (CALL and PUT)."""
    base_candles = [
        make_candle("1.1000", "1.1005", "1.0995", "1.1002", minute_offset=-4),
        make_candle("1.1002", "1.1006", "1.0998", "1.1001", minute_offset=-3),
        make_candle("1.1001", "1.1004", "1.0997", "1.1003", minute_offset=-2),
        make_candle("1.1003", "1.1007", "1.0999", "1.1004", minute_offset=-1),
    ]

    # CALL Breakout: solid body breaking above micro_high (1.1007)
    break_call = make_candle(
        open_price="1.1004",
        high_price="1.1022",
        low_price="1.1003",
        close_price="1.1020",
        minute_offset=0,
    )
    sig_call = BreakoutFlowTrigger.evaluate(base_candles + [break_call])
    assert sig_call is not None
    assert sig_call.direction is Direction.CALL
    assert "break_high" in sig_call.details

    # PUT Breakout: solid body breaking below micro_low (1.0995)
    break_put = make_candle(
        open_price="1.1000",
        high_price="1.1001",
        low_price="1.0980",
        close_price="1.0982",
        minute_offset=0,
    )
    sig_put = BreakoutFlowTrigger.evaluate(base_candles + [break_put])
    assert sig_put is not None
    assert sig_put.direction is Direction.PUT
    assert "break_low" in sig_put.details


def test_bollinger_exhaustion_trigger() -> None:
    """Scenario 3: Bollinger (20, 2.2) + RSI 7 Extreme Exhaustion."""
    # Build series with consecutive downward candles to plunge RSI(7) below 22
    candles: list[MarketCandle] = []
    p = Decimal("1.1000")
    for i in range(25):
        if i >= 16:
            p_next = p - Decimal("0.0006")
            c = make_candle(
                str(p),
                str(p + Decimal("0.0001")),
                str(p_next - Decimal("0.0001")),
                str(p_next),
                i - 25,
            )
        else:
            p_next = p + Decimal("0.0001")
            c = make_candle(
                str(p),
                str(p_next + Decimal("0.0001")),
                str(p),
                str(p_next),
                i - 25,
            )
        p = p_next
        candles.append(c)

    sig = BollingerExhaustionTrigger.evaluate(candles)
    assert sig is not None
    assert sig.direction is Direction.CALL
    assert "rsi7" in sig.details


def test_ema_pullback_engulf_trigger() -> None:
    """Scenario 4: EMA 14 x EMA 28 Pullback Engulfing."""
    candles = generate_candle_series(35, trend="bull")
    # Last candle was pullback red, current candle is strong green engulfing
    last_close = float(candles[-1].close)
    c_pullback = make_candle(
        str(last_close),
        str(last_close + 0.0001),
        str(last_close - 0.0005),
        str(last_close - 0.0004),
        minute_offset=1,
    )
    c_engulf = make_candle(
        str(last_close - 0.0004),
        str(last_close + 0.0010),
        str(last_close - 0.0004),
        str(last_close + 0.0008),
        minute_offset=2,
    )
    sig = EmaPullbackEngulfTrigger.evaluate(candles + [c_pullback, c_engulf])
    assert sig is not None
    assert sig.direction is Direction.CALL
    assert "engulf_call" in sig.details


def test_trigger_ambiguity_veto() -> None:
    """Anti-Contradiction: Conflicting triggers on the same bar trigger VETO_TRIGGER_AMBIGUITY."""
    strategy = IQOptionHackChinoStrategy()
    t_call = TriggerSignal("Rompimento", Direction.CALL, "breakout")
    t_put = TriggerSignal("Exaustão", Direction.PUT, "exhaustion")

    m_neutral = ModelEstimate("M1", 0.55, 0.45, None, 1.0, "neutral")

    ensemble = strategy.arbitrate_models((m_neutral,), triggers=(t_call, t_put))
    assert ensemble.has_conflict is True
    assert ensemble.direction is None
    assert ensemble.rejection_reason == "VETO_TRIGGER_AMBIGUITY"


def test_confluence_trigger_and_models() -> None:
    """Confluence: Active trigger validated by quant committee produces signal."""
    strategy = IQOptionHackChinoStrategy()
    t_call = TriggerSignal("Pavio Rejeição", Direction.CALL, "sweep")

    m1 = ModelEstimate("M1", 0.60, 0.40, Direction.CALL, 1.0, "call")
    m2 = ModelEstimate("M2", 0.59, 0.41, Direction.CALL, 1.0, "call")
    m3 = ModelEstimate("M3", 0.52, 0.48, None, 1.0, "neutral")

    ensemble = strategy.arbitrate_models((m1, m2, m3), triggers=(t_call,))
    assert ensemble.direction is Direction.CALL
    assert ensemble.has_conflict is False
    assert ensemble.ensemble_prob >= 0.565
    assert len(ensemble.active_triggers) == 1


def test_hack_chino_strategy_warmup_and_evaluation() -> None:
    strategy = IQOptionHackChinoStrategy()
    context = RuntimeContext(
        strategy_id=IQOPTION_HACK_CHINO_STRATEGY_ID,
        strategy_version="1.0.0",
        broker=Broker.IQ_OPTION,
        account_id="demo",
        product="BINARY_OPTION",
        symbol="EURUSD",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    short_series = generate_candle_series(20)
    decision_warmup = strategy.evaluate_closed_candles(short_series, context)
    assert decision_warmup.direction is None
    assert decision_warmup.rsi == Decimal("50.0")
    assert decision_warmup.stage.startswith("WARMING_UP")

    full_series = generate_candle_series(50, trend="bull")
    decision_full = strategy.evaluate_closed_candles(full_series, context)
    assert decision_full.ensemble is not None
    assert isinstance(decision_full.rsi, Decimal)
