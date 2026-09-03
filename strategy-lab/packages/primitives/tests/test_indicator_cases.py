"""R-PRIM-4: three hand-constructed behavior cases for every catalog indicator."""

from __future__ import annotations

from decimal import Decimal

from primitives.base import Candle, Indicator, Output
from primitives.confirm import (
    CandleRejection,
    RSIDivergence,
    RSIExtreme,
    StochCross,
    TickVolumeRatio,
)
from primitives.regime import ADX, BBWidthRatio, EMAAlignment, SessionWindow
from primitives.trigger import BBCloseOutside, EMAPullback, LevelTouch, QuadrantMajority, RangeBreak

BASE_TS = 1_800_000_000


def make_candle(
    index: int,
    close: str,
    *,
    open_: str | None = None,
    high: str | None = None,
    low: str | None = None,
    volume: int = 10,
) -> Candle:
    c = Decimal(close)
    o = c if open_ is None else Decimal(open_)
    h = max(o, c) if high is None else Decimal(high)
    low_value = min(o, c) if low is None else Decimal(low)
    return Candle(ts=BASE_TS + index * 60, o=o, h=h, l=low_value, c=c, tick_vol=volume)


def last_output(indicator: Indicator, candles: list[Candle]) -> Output:
    result = None
    for item in candles:
        result = indicator.update(item)
    assert result is not None
    return result


def price_stream(values: list[int], *, falling: bool = False) -> list[Candle]:
    result = []
    for index, raw in enumerate(values):
        value = -raw if falling else raw
        close = Decimal(100) + Decimal(value)
        result.append(make_candle(index, str(close), high=str(close + 1), low=str(close - 1)))
    return result


def test_adx_three_manual_trends() -> None:
    rising = last_output(ADX(period=2), price_stream(list(range(6))))
    falling = last_output(ADX(period=2), price_stream(list(range(6)), falling=True))
    flat = last_output(ADX(period=2), price_stream([0] * 6))
    assert (rising.value, rising.meta["plus_di"], rising.meta["minus_di"]) == (
        Decimal(100),
        Decimal(50),
        Decimal(0),
    )
    assert (falling.value, falling.meta["plus_di"], falling.meta["minus_di"]) == (
        Decimal(100),
        Decimal(0),
        Decimal(50),
    )
    assert flat.value == Decimal(0)


def test_bb_width_ratio_three_manual_shapes() -> None:
    flat = last_output(BBWidthRatio(length=2, median_length=2), price_stream([0, 0, 0]))
    expanding = last_output(BBWidthRatio(length=2, median_length=2), price_stream([0, 1, 4]))
    contracting = last_output(BBWidthRatio(length=2, median_length=2), price_stream([0, 4, 5]))
    assert flat.value == Decimal(0)
    assert expanding.value is not None and expanding.value > Decimal(1)
    assert contracting.value is not None and contracting.value < Decimal(1)


def test_ema_alignment_three_manual_trends() -> None:
    rising = last_output(EMAAlignment(short=2, medium=3, long=4), price_stream(list(range(6))))
    falling = last_output(
        EMAAlignment(short=2, medium=3, long=4), price_stream(list(range(6)), falling=True)
    )
    flat = last_output(EMAAlignment(short=2, medium=3, long=4), price_stream([0] * 6))
    assert rising.direction == "call"
    assert falling.direction == "put"
    assert flat.direction == "none"


def test_session_window_three_manual_times() -> None:
    day = SessionWindow(start_minute=60, end_minute=120)
    overnight = SessionWindow(start_minute=1380, end_minute=60)
    midnight = Candle(
        ts=1_800_057_600, o=Decimal(1), h=Decimal(1), l=Decimal(1), c=Decimal(1), tick_vol=1
    )
    at_0130 = midnight.model_copy(update={"ts": midnight.ts + 90 * 60})
    at_0300 = midnight.model_copy(update={"ts": midnight.ts + 180 * 60})
    assert day.update(at_0130).value == Decimal(1)
    assert day.update(at_0300).value == Decimal(0)
    assert overnight.update(midnight).value == Decimal(1)


def test_bb_close_outside_three_manual_positions() -> None:
    def decision(final: str) -> str:
        return last_output(
            BBCloseOutside(length=2), price_stream([0, 0]) + [make_candle(2, final)]
        ).direction

    assert decision("99") == "call"
    assert decision("101") == "put"
    assert decision("100") == "none"


def test_ema_pullback_three_manual_positions() -> None:
    base = [make_candle(index, "100") for index in range(2)]
    call = last_output(
        EMAPullback(period=2, tolerance=Decimal(0)),
        base + [make_candle(2, "101", open_="100", low="100")],
    )
    put = last_output(
        EMAPullback(period=2, tolerance=Decimal(0)),
        base + [make_candle(2, "99", open_="100", high="100")],
    )
    neutral = last_output(
        EMAPullback(period=2, tolerance=Decimal(0)), base + [make_candle(2, "100")]
    )
    assert call.direction == "call"
    assert put.direction == "put"
    assert neutral.direction == "none"


def test_level_touch_three_manual_positions() -> None:
    indicator = LevelTouch(support=Decimal(99), resistance=Decimal(101), tolerance=Decimal("0.1"))
    call = indicator.update(make_candle(0, "99.5", open_="99.2", high="100", low="99"))
    put = indicator.update(make_candle(1, "100.5", open_="100.8", high="101", low="100"))
    none = indicator.update(make_candle(2, "100", high="100.5", low="99.5"))
    assert (call.direction, put.direction, none.direction) == ("call", "put", "none")


def test_range_break_three_manual_positions() -> None:
    prior = [
        make_candle(0, "100", high="101", low="99"),
        make_candle(1, "100", high="101", low="99"),
    ]
    assert (
        last_output(
            RangeBreak(length=2), prior + [make_candle(2, "102", high="102", low="100")]
        ).direction
        == "call"
    )
    assert (
        last_output(
            RangeBreak(length=2), prior + [make_candle(2, "98", high="100", low="98")]
        ).direction
        == "put"
    )
    assert last_output(RangeBreak(length=2), prior + [make_candle(2, "100")]).direction == "none"


def test_quadrant_majority_three_manual_votes() -> None:
    def vote(bodies: list[str]) -> str:
        indicator = QuadrantMajority(window=3)
        result = None
        for offset, body in zip((2, 3, 4), bodies, strict=True):
            close = "101" if body == "call" else "99" if body == "put" else "100"
            result = indicator.update(make_candle(offset, close, open_="100"))
        assert result is not None
        return result.direction

    assert vote(["call", "call", "put"]) == "call"
    assert vote(["put", "put", "call"]) == "put"
    assert vote(["call", "put", "none"]) == "none"


def test_candle_rejection_three_manual_shapes() -> None:
    indicator = CandleRejection(max_body_ratio=Decimal("0.35"), min_wick_ratio=Decimal("0.5"))
    call = indicator.update(make_candle(0, "100.2", open_="100", high="100.3", low="99"))
    put = indicator.update(make_candle(1, "99.8", open_="100", high="101", low="99.7"))
    none = indicator.update(make_candle(2, "101", open_="100", high="101", low="100"))
    assert (call.direction, put.direction, none.direction) == ("call", "put", "none")


def test_rsi_extreme_three_manual_trends() -> None:
    assert last_output(RSIExtreme(period=2), price_stream([0, 1, 2])).direction == "put"
    assert (
        last_output(RSIExtreme(period=2), price_stream([0, 1, 2], falling=True)).direction == "call"
    )
    assert last_output(RSIExtreme(period=2), price_stream([0, 0, 0])).direction == "none"


def test_stoch_cross_three_manual_paths() -> None:
    def cross(values: list[int]):
        candles = [make_candle(i, str(v), high="10", low="0") for i, v in enumerate(values)]
        return last_output(StochCross(k_period=2, d_period=2), candles)

    assert cross([2, 2, 2, 8]).direction == "call"
    assert cross([8, 8, 8, 2]).direction == "put"
    assert cross([5, 5, 5, 5]).direction == "none"


def test_rsi_divergence_three_manual_paths() -> None:
    def divergence(values: list[int]):
        return last_output(RSIDivergence(period=2, lookback=2), price_stream(values))

    assert divergence([0, -10, -5, -10, -6]).direction == "call"
    assert divergence([0, 10, 5, 10, 6]).direction == "put"
    assert divergence([0, 0, 0, 0, 0]).direction == "none"


def test_tick_volume_ratio_three_manual_cases() -> None:
    base = [make_candle(0, "100", volume=10), make_candle(1, "100", volume=10)]
    call = last_output(
        TickVolumeRatio(length=2, minimum_ratio=Decimal("1.5")),
        base + [make_candle(2, "101", open_="100", volume=20)],
    )
    put = last_output(
        TickVolumeRatio(length=2, minimum_ratio=Decimal("1.5")),
        base + [make_candle(2, "99", open_="100", volume=20)],
    )
    none = last_output(
        TickVolumeRatio(length=2, minimum_ratio=Decimal("1.5")),
        base + [make_candle(2, "101", open_="100", volume=10)],
    )
    assert (call.direction, put.direction, none.direction) == ("call", "put", "none")
