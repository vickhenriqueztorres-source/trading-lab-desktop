"""Unit tests for SPRT package (R-BOT-7)."""

from __future__ import annotations

import random
from decimal import Decimal

from packages.sprt import SPRT, Decision


def test_sprt_bounds_and_initial_state() -> None:
    sprt = SPRT(p0=Decimal("0.58"), p1=Decimal("0.54"), alpha="0.05", beta="0.05")
    assert sprt.p0 == Decimal("0.58")
    assert sprt.p1 == Decimal("0.54")
    assert sprt.n == 0
    assert sprt.wins == 0
    assert sprt.llr == Decimal("0")
    assert sprt.decision == Decision.CONTINUE
    assert sprt.ever_rejected is False

    # A = ln(0.95 / 0.05) ~= 2.94443898
    # B = ln(0.05 / 0.95) ~= -2.94443898
    assert sprt.a_bound > Decimal("2.9")
    assert sprt.b_bound < Decimal("-2.9")
    assert sprt.a_bound == -sprt.b_bound


def test_sprt_serialization_cycle() -> None:
    sprt = SPRT(p0="0.58", p1="0.54")
    sprt.update(won=True)
    sprt.update(won=False)
    assert sprt.n == 2
    assert sprt.wins == 1

    data = sprt.to_dict()
    restored = SPRT.from_dict(data)
    assert restored.p0 == sprt.p0
    assert restored.p1 == sprt.p1
    assert restored.n == sprt.n
    assert restored.wins == sprt.wins
    assert restored.llr == sprt.llr
    assert restored.decision == sprt.decision
    assert restored.ever_rejected == sprt.ever_rejected


def test_sprt_does_not_reject_under_h0_in_1000_ops() -> None:
    """Under H0 (p_real = p0), false rejection rate must be <= alpha (5%)."""
    p0 = Decimal("0.58")
    p1 = Decimal("0.46")
    rejections = 0
    trials = 100

    for seed in range(trials):
        rng = random.Random(seed + 42)
        sprt = SPRT(p0=p0, p1=p1, alpha="0.05", beta="0.05")
        for _ in range(1000):
            won = rng.random() < float(p0)
            dec = sprt.update(won)
            if dec == Decision.REJECT_H0:
                rejections += 1
                break
            if dec == Decision.ACCEPT_H0:
                break

    # False rejection rate should be <= 5% (with small sampling margin <= 8%)
    assert rejections <= 8, f"False rejection rate too high: {rejections}/{trials}"


def test_sprt_rejects_under_h1_in_less_than_120_ops_median() -> None:
    """Under H1 (p_real = p1), median operations to reject must be < 120 ops."""
    p0 = Decimal("0.58")
    p1 = Decimal("0.46")
    trials = 100
    ops_to_reject = []

    for seed in range(trials):
        rng = random.Random(seed + 1000)
        sprt = SPRT(p0=p0, p1=p1, alpha="0.05", beta="0.05")
        for op in range(1, 1000):
            won = rng.random() < float(p1)
            dec = sprt.update(won)
            if dec == Decision.REJECT_H0:
                ops_to_reject.append(op)
                break
        else:
            ops_to_reject.append(1000)

    ops_to_reject.sort()
    median_ops = ops_to_reject[len(ops_to_reject) // 2]
    assert median_ops < 120, f"Expected median ops < 120, got {median_ops}"
