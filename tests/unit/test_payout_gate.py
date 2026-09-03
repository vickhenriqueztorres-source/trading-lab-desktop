"""Unit tests for PayoutGate (R-BOT-6)."""

from __future__ import annotations

from decimal import Decimal

from apps.core.payout_gate import (
    PAYOUT_BELOW_VALIDATED_EDGE,
    PayoutGate,
)


def test_payout_gate_allowed_when_edge_and_payout_sufficient() -> None:
    # payout = 0.85 (85%), p_min_now = 1 / 1.85 ~= 0.54054
    # required_wilson = 0.54054 + 0.015 = 0.55554
    # wilson_lower = 0.560 -> sufficient!
    res = PayoutGate.check_payout(
        current_payout=Decimal("0.85"),
        wilson_lower=Decimal("0.560"),
        payout_min=Decimal("0.82"),
    )
    assert res.allowed is True
    assert res.reason_code == ""
    assert res.message == ""
    assert res.payout == Decimal("0.85")
    assert res.payout_min == Decimal("0.82")


def test_payout_gate_blocks_when_current_payout_below_payout_min() -> None:
    # Current payout 80% < payout_min 85% -> blocked even if wilson_lower is very high
    res = PayoutGate.check_payout(
        current_payout=Decimal("0.80"),
        wilson_lower=Decimal("0.650"),
        payout_min=Decimal("0.85"),
    )
    assert res.allowed is False
    assert res.reason_code == PAYOUT_BELOW_VALIDATED_EDGE
    assert res.message == "Opera com payout ≥ 85%. Agora: 80% — aguardando."


def test_payout_gate_blocks_when_wilson_lower_below_break_even_plus_edge() -> None:
    # payout = 0.70 (70%), p_min_now = 1 / 1.70 = 0.5882
    # required_wilson = 0.5882 + 0.015 = 0.6032
    # wilson_lower = 0.590 < 0.6032 -> blocked!
    res = PayoutGate.check_payout(
        current_payout=Decimal("0.70"),
        wilson_lower=Decimal("0.590"),
        payout_min=Decimal("0.70"),
    )
    assert res.allowed is False
    assert res.reason_code == PAYOUT_BELOW_VALIDATED_EDGE
    assert res.message == "Opera com payout ≥ 70%. Agora: 70% — aguardando."


def test_payout_gate_handles_integer_percentages() -> None:
    # Accepts 85 (85%) and 80 (80%)
    res = PayoutGate.check_payout(
        current_payout=80,
        wilson_lower=Decimal("0.58"),
        payout_min=85,
    )
    assert res.allowed is False
    assert res.reason_code == PAYOUT_BELOW_VALIDATED_EDGE
    assert res.message == "Opera com payout ≥ 85%. Agora: 80% — aguardando."


def test_payout_gate_zero_or_negative_payout_blocked() -> None:
    res = PayoutGate.check_payout(
        current_payout=0,
        wilson_lower=Decimal("0.56"),
        payout_min=Decimal("0.85"),
    )
    assert res.allowed is False
    assert res.reason_code == PAYOUT_BELOW_VALIDATED_EDGE
    assert res.message == "Opera com payout ≥ 85%. Agora: 0% — aguardando."
