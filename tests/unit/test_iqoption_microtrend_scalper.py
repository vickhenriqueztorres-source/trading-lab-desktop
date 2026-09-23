from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies.iqoption_microtrend_scalper import (
    IQOPTION_MICROTREND_SCALPER_STRATEGY_ID,
    IQOptionMicrotrendScalperStrategy,
    iqoption_microtrend_scalper_manifest,
)
from packages.strategies.models import RuntimeContext

NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=UTC)


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


def test_microtrend_scalper_manifest() -> None:
    manifest = iqoption_microtrend_scalper_manifest()
    assert manifest.strategy_id == IQOPTION_MICROTREND_SCALPER_STRATEGY_ID
    assert manifest.version == "2.0.0"
    assert manifest.warmup_candles == 35
    assert manifest.supported_timeframes == (60,)


def test_microtrend_scalper_call_signal() -> None:
    strategy = IQOptionMicrotrendScalperStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD",
        account_id="practice",
        strategy_id=IQOPTION_MICROTREND_SCALPER_STRATEGY_ID,
        strategy_version="2.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # 30 background candles creating an uptrend (EMA10 > EMA30)
    candles: list[MarketCandle] = []
    base = Decimal("1.0500")
    for i in range(30):
        o = base + Decimal(i) * Decimal("0.0003")
        c = o + Decimal("0.0002")
        h = c + Decimal("0.0001")
        l = o - Decimal("0.0001")
        candles.append(make_candle(str(o), str(h), str(l), str(c), i))

    # Candles 30 and 31: small pullback (down candles) so RSI(5) has healthy losses
    # Candle 30: 1.0590 -> 1.0585 (-0.0005)
    # Candle 31: 1.0585 -> 1.0582 (-0.0003)
    candles.append(make_candle("1.0590", "1.0592", "1.0584", "1.0585", 30))
    candles.append(make_candle("1.0585", "1.0587", "1.0581", "1.0582", 31))

    # 3 bullish candles (t-2, t-1, t):
    # Candle 32 (t-2): 1.0582 -> 1.0590 (+0.0008)
    # Candle 33 (t-1): 1.0590 -> 1.0598 (+0.0008)
    # Candle 34 (t):   1.0598 -> 1.0606 (+0.0008)
    # Total gains in 5 candles = 0.0024, losses = 0.0008. RS = 3.0. RSI(5) = 75 (in [55, 80]!)
    candles.append(make_candle("1.0582", "1.0591", "1.0581", "1.0590", 32))
    candles.append(make_candle("1.0590", "1.0599", "1.0589", "1.0598", 33))
    # Candle 34: open 1.0598, high 1.0607, low 1.0597, close 1.0606
    # range = 0.0010. close_pos = (1.0606 - 1.0597)/0.0010 = 0.90 (>= 0.75)
    # body strength = 0.0008 / 0.0010 = 0.80 (>= 0.45)
    candles.append(make_candle("1.0598", "1.0607", "1.0597", "1.0606", 34))

    assert len(candles) == 35
    decision = strategy.evaluate_decision(candles, context)
    assert decision.direction == Direction.CALL
    assert decision.is_consecutive_same_direction is True
    assert decision.mean_body_strength >= Decimal("0.45")
    assert decision.close_position is not None and decision.close_position >= Decimal("0.75")
    assert Decimal("55") <= decision.rsi <= Decimal("80")
    assert decision.ema10 is not None and decision.ema30 is not None and decision.ema10 > decision.ema30
    assert decision.duration_candles == 1
    assert decision.reason_code == "MICROTREND_BULLISH_CONTINUATION_CALL"


def test_microtrend_scalper_put_signal() -> None:
    strategy = IQOptionMicrotrendScalperStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD",
        account_id="practice",
        strategy_id=IQOPTION_MICROTREND_SCALPER_STRATEGY_ID,
        strategy_version="2.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # 30 background candles creating a downtrend (EMA10 < EMA30)
    candles: list[MarketCandle] = []
    base = Decimal("1.0800")
    for i in range(30):
        o = base - Decimal(i) * Decimal("0.0003")
        c = o - Decimal("0.0002")
        h = o + Decimal("0.0001")
        l = c - Decimal("0.0001")
        candles.append(make_candle(str(o), str(h), str(l), str(c), i))

    # Candles 30 and 31: small bounce (up candles) so RSI(5) has healthy gains
    candles.append(make_candle("1.0710", "1.0716", "1.0709", "1.0715", 30))
    candles.append(make_candle("1.0715", "1.0719", "1.0714", "1.0718", 31))

    # 3 bearish candles (t-2, t-1, t):
    candles.append(make_candle("1.0718", "1.0719", "1.0709", "1.0710", 32))
    candles.append(make_candle("1.0710", "1.0711", "1.0701", "1.0702", 33))
    # Candle 34: open 1.0702, high 1.0703, low 1.0693, close 1.0694
    # range = 0.0010. close_pos = (1.0694 - 1.0693)/0.0010 = 0.10 (<= 0.25)
    # body strength = 0.0008 / 0.0010 = 0.80 (>= 0.45)
    candles.append(make_candle("1.0702", "1.0703", "1.0693", "1.0694", 34))

    assert len(candles) == 35
    decision = strategy.evaluate_decision(candles, context)
    assert decision.direction == Direction.PUT
    assert decision.is_consecutive_same_direction is True
    assert decision.mean_body_strength >= Decimal("0.45")
    assert decision.close_position is not None and decision.close_position <= Decimal("0.25")
    assert Decimal("20") <= decision.rsi <= Decimal("45")
    assert decision.ema10 is not None and decision.ema30 is not None and decision.ema10 < decision.ema30
    assert decision.duration_candles == 1
    assert decision.reason_code == "MICROTREND_BEARISH_CONTINUATION_PUT"


def test_microtrend_scalper_exclusion_range_exceeded() -> None:
    strategy = IQOptionMicrotrendScalperStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD",
        account_id="practice",
        strategy_id=IQOPTION_MICROTREND_SCALPER_STRATEGY_ID,
        strategy_version="2.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    candles: list[MarketCandle] = []
    base = Decimal("1.0500")
    for i in range(32):
        o = base + Decimal(i) * Decimal("0.0002")
        c = o + Decimal("0.0001")
        h = c + Decimal("0.0001")
        l = o - Decimal("0.0001")
        candles.append(make_candle(str(o), str(h), str(l), str(c), i))

    candles.append(make_candle("1.0570", "1.0574", "1.0569", "1.0573", 32))
    candles.append(make_candle("1.0573", "1.0577", "1.0572", "1.0576", 33))
    # Massive spike on candle 34: range = 1.0620 - 1.0570 = 0.0050 (> 2.5 * A20)
    candles.append(make_candle("1.0576", "1.0620", "1.0570", "1.0618", 34))

    decision = strategy.evaluate_decision(candles, context)
    assert decision.direction is None
    assert decision.reason_code == "EXCLUSION_AMPLITUDE_EXCEEDED"


def test_microtrend_scalper_rejected_ema_mismatch() -> None:
    strategy = IQOptionMicrotrendScalperStrategy()
    context = RuntimeContext(
        broker=Broker.IQ_OPTION,
        symbol="EURUSD",
        account_id="practice",
        strategy_id=IQOPTION_MICROTREND_SCALPER_STRATEGY_ID,
        strategy_version="2.0.0",
        product="BINARY_OPTION",
        timeframe_seconds=60,
        configuration_version="1.0.0",
    )

    # Severe downtrend (EMA10 will be < EMA30)
    candles: list[MarketCandle] = []
    base = Decimal("1.1000")
    for i in range(30):
        o = base - Decimal(i) * Decimal("0.0010")
        c = o - Decimal("0.0008")
        h = o + Decimal("0.0001")
        l = c - Decimal("0.0001")
        candles.append(make_candle(str(o), str(h), str(l), str(c), i))

    # Pullback candles 30 and 31 (down)
    candles.append(make_candle("1.0700", "1.0702", "1.0694", "1.0695", 30))
    candles.append(make_candle("1.0695", "1.0697", "1.0691", "1.0692", 31))

    # 3 small bullish candles producing healthy RSI in [55, 80], but counter-trend against EMA
    candles.append(make_candle("1.0692", "1.0701", "1.0691", "1.0700", 32))
    candles.append(make_candle("1.0700", "1.0709", "1.0699", "1.0708", 33))
    candles.append(make_candle("1.0708", "1.0717", "1.0707", "1.0716", 34))

    decision = strategy.evaluate_decision(candles, context)
    assert decision.direction is None
    # EMA10 < EMA30, so CALL is rejected because trend doesn't align
    assert decision.reason_code == "REJECTED_EMA_TREND_MISMATCH"
