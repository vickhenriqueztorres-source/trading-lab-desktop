"""Tests for statistical research gates and coin flip CI test (R-RES-7, R-RES-8, R-RES-10)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np
from primitives import Candle, Category, Indicator, Output, ParamRange
from strategy_lab.research.candidate import Candidate
from strategy_lab.research.gates.approve import approve_candidate
from strategy_lab.research.gates.multiple_testing import (
    benjamini_hochberg,
    fdr_candidate_check,
)
from strategy_lab.research.gates.neighborhood import evaluate_neighborhood
from strategy_lab.research.gates.walk_forward import evaluate_stability
from strategy_lab.research.gates.wilson import wilson_lower, wilson_lower_p
from strategy_lab.research.payout_lookup import PayoutLookup, PayoutPoint
from strategy_lab.research.replay_simulator import Trade, TradeLog
from strategy_lab.research.synthetic import BASE_TS, edge_series, random_walk


class SpikeTrigger(Indicator):
    """Trigger indicator with edge only at length == 20."""

    category = Category.TRIGGER
    name = "spike_trigger"
    param_spec = {"length": ParamRange(min=10, max=50, step=1, kind="int")}

    def __init__(self, length: int = 20) -> None:
        self.length = int(length)

    @property
    def warmup_required(self) -> int:
        return 1

    def reset(self) -> None:
        pass

    def update(self, candle: Candle) -> Output:
        # If length is exactly 20, follow candle body; else opposite or none
        direction: Any = "none"
        if self.length == 20:
            direction = "call" if candle.c > candle.o else "put"
        else:
            direction = "put" if candle.c > candle.o else "call"
        return Output(direction=direction, value=candle.c - candle.o, meta={})


class AlwaysActiveRegime(Indicator):
    category = Category.REGIME
    name = "always_active"
    param_spec = {}

    @property
    def warmup_required(self) -> int:
        return 1

    def reset(self) -> None:
        pass

    def update(self, candle: Candle) -> Output:
        return Output(direction="none", value=Decimal("1"), meta={})


class FollowConfirm(Indicator):
    category = Category.CONFIRM
    name = "follow_confirm"
    param_spec = {}

    @property
    def warmup_required(self) -> int:
        return 1

    def reset(self) -> None:
        pass

    def update(self, candle: Candle) -> Output:
        direction: Any = "call" if candle.c > candle.o else "put"
        return Output(direction=direction, value=Decimal("1"), meta={})


GATES_TEST_REGISTRY: dict[str, type[Indicator]] = {
    "always_active": AlwaysActiveRegime,
    "spike_trigger": SpikeTrigger,
    "follow_confirm": FollowConfirm,
}


def _full_payout_lookup() -> PayoutLookup:
    return PayoutLookup(
        [
            PayoutPoint(
                "EURUSD-OTC",
                BASE_TS - BASE_TS % 3600 + offset * 3600,
                Decimal("0.85"),
                1,
            )
            for offset in range(200)
        ]
    )


def test_wilson_matches_reference_values() -> None:
    """R-RES-8: valida limites inferiores de Wilson 95% contra valores canônicos."""
    # n=100, w=60 -> 0.5020...
    w100 = wilson_lower(60, 100)
    assert Decimal("0.5019") <= w100 <= Decimal("0.5021")

    # n=500, w=300 -> 0.5564...
    w500 = wilson_lower(300, 500)
    assert Decimal("0.5564") <= w500 <= Decimal("0.5566")

    # n=1000, w=550 -> 0.5190...
    w1000 = wilson_lower(550, 1000)
    assert Decimal("0.5189") <= w1000 <= Decimal("0.5191")

    # Degenerate cases
    assert wilson_lower(0, 100) == Decimal("0")
    assert wilson_lower(50, 0) == Decimal("0")
    assert wilson_lower_p(Decimal("0"), 100) == Decimal("0")


def test_fdr_uses_total_candidate_count() -> None:
    """R-RES-7: threshold de FDR depende do número total N de candidatos avaliados na rodada."""
    p_val = Decimal("0.0001")
    rank = 1

    # In a small round of N=10, critical threshold = (1/10)*0.05 = 0.005 -> passes
    res_small = fdr_candidate_check(p_val, rank=rank, total_candidates=10)
    assert res_small.passed is True
    assert res_small.critical_value == Decimal("0.005")

    # In a large round of N=5000, critical threshold = (1/5000)*0.05 = 0.00001 -> fails
    res_large = fdr_candidate_check(p_val, rank=rank, total_candidates=5000)
    assert res_large.passed is False
    assert res_large.critical_value == Decimal("0.00001")

    # Multiple candidates vector check: rank 1 threshold is (1/100)*0.05 = 0.0005
    p_vals = [Decimal("0.0004"), Decimal("0.02"), Decimal("0.04")]
    passed = benjamini_hochberg(p_vals, total_candidates=100)
    assert passed[0] is True
    assert passed[1] is False
    assert passed[2] is False


def test_unstable_edge_fails_stability() -> None:
    """R-RES-7: edge instável (0.62 em metade e 0.50 na outra) é reprovado."""
    p_min = Decimal("0.5405")

    # Window with win rate < p_min fails
    p_hats = [Decimal("0.62"), Decimal("0.62"), Decimal("0.50"), Decimal("0.50")]
    stab = evaluate_stability(p_hats, p_min=p_min)
    assert stab.passed is False
    assert stab.reason == "WINDOW_BELOW_PMIN"

    # Windows with all >= p_min but std >= 3 pp (0.03) fail
    unstable_high = [Decimal("0.64"), Decimal("0.56"), Decimal("0.64"), Decimal("0.56")]
    stab2 = evaluate_stability(unstable_high, p_min=Decimal("0.54"))
    assert stab2.passed is False
    assert stab2.reason == "STD_EXCEEDS_3PP"
    assert stab2.std_p_hat >= Decimal("0.03")

    # Stable windows pass
    stable = [Decimal("0.60"), Decimal("0.61"), Decimal("0.60"), Decimal("0.59")]
    stab3 = evaluate_stability(stable, p_min=Decimal("0.54"))
    assert stab3.passed is True
    assert stab3.std_p_hat < Decimal("0.03")


def test_neighborhood_spike_fails() -> None:
    """R-RES-7: candidato com edge restrito a um ponto de parâmetro isolado falha na vizinhança."""
    # Create candles where following body matches current body with 60% probability
    candles = edge_series(seed=123, length=600, win_probability_pct=60)
    lookup = _full_payout_lookup()
    p_min = Decimal("0.5405")

    candidate = Candidate(
        family="spike_family",
        regime="always_active",
        trigger="spike_trigger",
        confirm="follow_confirm",
        params={"spike_trigger": {"length": 20}},
        asset="EURUSD-OTC",
    )

    # SpikeTrigger with length=20 has edge; neighbors length=17 and length=23 will have ~40%
    result = evaluate_neighborhood(candidate, candles, lookup, p_min, registry=GATES_TEST_REGISTRY)
    assert result.passed is False
    assert result.reason == "MEDIAN_BELOW_THRESHOLD"
    assert result.median_p_hat < result.required_threshold


def test_injected_edge_is_approved() -> None:
    """R-RES-8: série com p=0,60 estável passa por todos os portões e é aprovada."""
    lookup = _full_payout_lookup()
    p_min = Decimal("0.53")

    # Generate series with 60% win rate uniformly distributed across blocks
    rng = np.random.default_rng(42)
    length = 800
    directions = ["call" if int(rng.integers(0, 2)) == 1 else "put" for _ in range(length)]
    closes = [Decimal("100")]
    wins_pattern: list[bool] = []
    for _ in range(length // 10):
        block = [True] * 6 + [False] * 4
        rng.shuffle(block)
        wins_pattern.extend(block)
    while len(wins_pattern) < length:
        wins_pattern.append(True)

    for index in range(length - 1):
        wins = wins_pattern[index]
        next_d = directions[index] if wins else ("put" if directions[index] == "call" else "call")
        closes.append(closes[-1] + (Decimal("0.01") if next_d == "call" else Decimal("-0.01")))

    candles: list[Candle] = []
    for index in range(length):
        c = closes[index]
        o = c - Decimal("0.005") if directions[index] == "call" else c + Decimal("0.005")
        h = max(o, c) + Decimal("0.02")
        lo = min(o, c) - Decimal("0.02")
        candles.append(Candle(ts=BASE_TS + index * 60, o=o, h=h, l=lo, c=c, tick_vol=100))

    class RobustTrigger(Indicator):
        category = Category.TRIGGER
        name = "robust_trigger"
        param_spec = {"length": ParamRange(min=10, max=50, step=1, kind="int")}

        def __init__(self, length: int = 20) -> None:
            self.length = length

        @property
        def warmup_required(self) -> int:
            return 1

        def reset(self) -> None:
            pass

        def update(self, c: Candle) -> Output:
            d: Any = "call" if c.c > c.o else "put"
            return Output(direction=d, value=c.c - c.o, meta={})

    reg = dict(GATES_TEST_REGISTRY)
    reg["robust_trigger"] = RobustTrigger
    cand_robust = Candidate(
        family="f_robust",
        regime="always_active",
        trigger="robust_trigger",
        confirm="follow_confirm",
        params={"robust_trigger": {"length": 20}},
        asset="EURUSD-OTC",
    )

    app_res = approve_candidate(
        cand_robust,
        candles,
        lookup,
        p_min,
        total_candidates=1,
        min_oos_trades=500,
        train_duration_s=200 * 60,
        test_duration_s=100 * 60,
        registry=reg,
    )

    assert app_res.approved is True
    assert app_res.wilson_lower >= p_min + Decimal("0.015")
    assert len(app_res.gate_results) == 5
    assert all(gate.passed for gate in app_res.gate_results)


def test_coin_flip_approves_zero() -> None:
    """R-RES-10 (CI intocável): 2.000 candidatos aleatórios sobre passeio aleatório -> 0 aprovados.

    Executado com 3 seeds distintos.
    """
    p_min = Decimal("0.5405")
    seeds = [101, 202, 303]
    lookup = _full_payout_lookup()

    for seed in seeds:
        candles = random_walk(seed=seed, length=600)
        rng = np.random.default_rng(seed)
        approved_count = 0

        # Simulate 2,000 candidate evaluations under random walk
        # On a random walk, p_hat is centered around 50%
        cand = Candidate(
            family="random_family",
            regime="always_active",
            trigger="spike_trigger",
            confirm="follow_confirm",
            asset="EURUSD-OTC",
        )

        for i in range(2000):
            # Generate random trade outcomes with 50% binomial distribution
            n_trades = int(rng.integers(500, 600))
            wins = int(rng.binomial(n_trades, 0.50))
            trades = tuple(
                Trade(
                    ts=BASE_TS + j * 60,
                    asset="EURUSD-OTC",
                    direction="call" if j % 2 == 0 else "put",
                    won=(j < wins),
                    payout_return_ratio=Decimal("0.85"),
                    profit_ratio=Decimal("0.85") if j < wins else Decimal("-1"),
                    update_count_at_signal=j,
                )
                for j in range(n_trades)
            )
            fake_log = TradeLog(trades=trades, excluded_missing_payout=0)

            res = approve_candidate(
                cand,
                candles,
                lookup,
                p_min,
                total_candidates=2000,
                candidate_rank=i + 1,
                min_oos_trades=500,
                trade_log=fake_log,
                registry=GATES_TEST_REGISTRY,
            )
            if res.approved:
                approved_count += 1

        assert approved_count == 0, (
            f"Seed {seed} approved {approved_count} candidates on coin flip!"
        )
