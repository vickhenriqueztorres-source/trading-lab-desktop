"""Deterministic synthetic generators for research tests (R-RES-10 partial)."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
from primitives import Candle
from primitives.base import Category, Direction, Indicator, Output

from strategy_lab.research.candidate import Candidate

BASE_TS = 1_700_000_040


def random_walk(seed: int, length: int, *, start: Decimal = Decimal("100")) -> list[Candle]:
    rng = np.random.default_rng(seed)
    price = start
    candles: list[Candle] = []
    for index in range(length):
        step = Decimal(int(rng.integers(-3, 4))) / Decimal("100")
        close = price + step
        candles.append(_candle(BASE_TS + index * 60, price, close))
        price = close
    return candles


def edge_series(seed: int, length: int, win_probability_pct: int = 60) -> list[Candle]:
    """Current body direction predicts the next close with a configurable percentage."""
    rng = np.random.default_rng(seed)
    directions: list[Direction] = [
        "call" if int(rng.integers(0, 2)) == 1 else "put" for _ in range(length)
    ]
    closes: list[Decimal] = [Decimal("100")]
    for index in range(length - 1):
        wins = int(rng.integers(0, 100)) < win_probability_pct
        next_direction = directions[index] if wins else _opposite(directions[index])
        closes.append(
            closes[-1] + (Decimal("0.01") if next_direction == "call" else Decimal("-0.01"))
        )
    candles: list[Candle] = []
    for index in range(length):
        close = closes[index]
        open_price = (
            close - Decimal("0.005") if directions[index] == "call" else close + Decimal("0.005")
        )
        candles.append(_candle(BASE_TS + index * 60, open_price, close))
    return candles


def reverse_oracle_lookahead(candles: list[Candle]) -> Decimal:
    """Deliberately illegal detector fixture: choose direction from t+1 and report p_hat."""
    wins = 0
    count = 0
    for current, following in zip(candles, candles[1:], strict=False):
        if following.c == current.c:
            continue
        direction: Direction = "call" if following.c > current.c else "put"
        wins += int(
            (direction == "call" and following.c > current.c)
            or (direction == "put" and following.c < current.c)
        )
        count += 1
    return Decimal(wins) / Decimal(count) if count else Decimal("0")


def _candle(ts: int, open_price: Decimal, close: Decimal) -> Candle:
    high = max(open_price, close) + Decimal("0.02")
    low = min(open_price, close) - Decimal("0.02")
    return Candle(ts=ts, o=open_price, h=high, l=low, c=close, tick_vol=100)


def _opposite(direction: Direction) -> Direction:
    return "put" if direction == "call" else "call"


class AlwaysRegime(Indicator):
    category = Category.REGIME
    name = "always_regime"
    param_spec = {}

    @property
    def warmup_required(self) -> int:
        return 1

    def reset(self) -> None:
        pass

    def update(self, candle: Candle) -> Output:
        return Output(direction="none", value=Decimal("1"), meta={"ts": Decimal(candle.ts)})


class BodyTrigger(Indicator):
    category = Category.TRIGGER
    name = "body_trigger"
    param_spec = {}

    @property
    def warmup_required(self) -> int:
        return 1

    def reset(self) -> None:
        pass

    def update(self, candle: Candle) -> Output:
        direction: Direction = (
            "call" if candle.c > candle.o else "put" if candle.c < candle.o else "none"
        )
        return Output(direction=direction, value=candle.c - candle.o, meta={})


class BodyConfirm(BodyTrigger):
    category = Category.CONFIRM
    name = "body_confirm"


def register_synthetic_primitives() -> None:
    from primitives.registry import REGISTRY

    REGISTRY["always_regime"] = AlwaysRegime
    REGISTRY["body_trigger"] = BodyTrigger
    REGISTRY["body_confirm"] = BodyConfirm


def make_injected_edge_candidate(asset: str = "EURUSD-OTC") -> Candidate:
    return Candidate(
        family="F1",
        regime="always_regime",
        trigger="body_trigger",
        confirm="body_confirm",
        params={
            "adx": {"period": 14},
            "bb_close_outside": {"length": 20, "k": Decimal("2.0")},
            "rsi_extreme": {"period": 7, "lower": 20, "upper": 80},
        },
        tf="M1",
        hours=(0, 24),
        asset=asset,
    )
