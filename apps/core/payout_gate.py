"""Broker payout pre-trade verification gate (R-BOT-6)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

PAYOUT_GATE_EDGE_OFFSET = Decimal("0.015")
PAYOUT_BELOW_VALIDATED_EDGE = "PAYOUT_BELOW_VALIDATED_EDGE"


@dataclass(frozen=True, slots=True)
class PayoutGateResult:
    allowed: bool
    reason_code: str
    message: str
    p_min_now: Decimal
    payout: Decimal
    payout_min: Decimal


def normalize_ratio(value: Decimal | float | str | int) -> Decimal:
    """Normalize percentage or ratio to [0.0, 1.0] scale."""
    dec = Decimal(str(value))
    if dec > Decimal(1):
        dec = dec / Decimal(100)
    return dec


class PayoutGate:
    """Evaluates whether current broker payout satisfies the statistical edge requirement."""

    @staticmethod
    def check_payout(
        current_payout: Decimal | float | str | int,
        wilson_lower: Decimal | float | str | int,
        payout_min: Decimal | float | str | int,
    ) -> PayoutGateResult:
        """Check if current payout provides sufficient edge before order dispatch."""
        try:
            payout = normalize_ratio(current_payout)
            p_min_manifest = normalize_ratio(payout_min)
            w_lower = Decimal(str(wilson_lower))
            if not all(x.is_finite() for x in (payout, p_min_manifest, w_lower)):
                raise ValueError("nonfinite payout evidence")
            if not (0 <= payout <= 1 and 0 <= p_min_manifest <= 1 and 0 < w_lower <= 1):
                raise ValueError("invalid payout evidence")
        except (ValueError, InvalidOperation):
            return PayoutGateResult(
                False,
                "PAYOUT_EVIDENCE_INVALID",
                "Payout inválido.",
                Decimal(1),
                Decimal(0),
                Decimal(0),
            )

        if payout <= Decimal(0):
            payout_min_pct = int(round(p_min_manifest * Decimal(100)))
            atual_pct = 0
            message = f"Opera com payout ≥ {payout_min_pct}%. Agora: {atual_pct}% — aguardando."
            return PayoutGateResult(
                allowed=False,
                reason_code=PAYOUT_BELOW_VALIDATED_EDGE,
                message=message,
                p_min_now=Decimal(1),
                payout=payout,
                payout_min=p_min_manifest,
            )

        p_min_now = Decimal(1) / (Decimal(1) + payout)
        edge_threshold = p_min_now + PAYOUT_GATE_EDGE_OFFSET

        blocked = (payout < p_min_manifest) or (w_lower < edge_threshold)

        if blocked:
            payout_min_pct = int(round(p_min_manifest * Decimal(100)))
            atual_pct = int(round(payout * Decimal(100)))
            message = f"Opera com payout ≥ {payout_min_pct}%. Agora: {atual_pct}% — aguardando."
            return PayoutGateResult(
                allowed=False,
                reason_code=PAYOUT_BELOW_VALIDATED_EDGE,
                message=message,
                p_min_now=p_min_now,
                payout=payout,
                payout_min=p_min_manifest,
            )

        return PayoutGateResult(
            allowed=True,
            reason_code="",
            message="",
            p_min_now=p_min_now,
            payout=payout,
            payout_min=p_min_manifest,
        )
