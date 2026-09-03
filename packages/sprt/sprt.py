"""Sequential Probability Ratio Test (SPRT) implementation for Trading Lab bot (R-BOT-7)."""

from __future__ import annotations

import math
from decimal import Decimal, getcontext, localcontext
from enum import StrEnum
from typing import Any

getcontext().prec = 28


class Decision(StrEnum):
    CONTINUE = "continue"
    ACCEPT_H0 = "accept_h0"  # Edge confirmed
    REJECT_H0 = "reject_h0"  # Edge lost -> demote to observation


class SPRT:
    """Wald's Sequential Probability Ratio Test for binary Bernoulli trials.

    H0: p = p0 (baseline rate / wilson_lower)
    H1: p = p1 (unacceptable rate / p_min)
    alpha = type I error (default: 0.05)
    beta = type II error (default: 0.05)
    """

    def __init__(
        self,
        p0: Decimal | float | str,
        p1: Decimal | float | str,
        alpha: Decimal | float | str = Decimal("0.05"),
        beta: Decimal | float | str = Decimal("0.05"),
    ) -> None:
        self._p0 = Decimal(str(p0))
        self._p1 = Decimal(str(p1))
        self._alpha = Decimal(str(alpha))
        self._beta = Decimal(str(beta))

        if not (Decimal(0) < self._p1 < self._p0 < Decimal(1)):
            raise ValueError(f"SPRT requires 0 < p1 < p0 < 1, got p1={self._p1}, p0={self._p0}")
        if not (Decimal(0) < self._alpha < Decimal("0.5")):
            raise ValueError(f"SPRT requires 0 < alpha < 0.5, got alpha={self._alpha}")
        if not (Decimal(0) < self._beta < Decimal("0.5")):
            raise ValueError(f"SPRT requires 0 < beta < 0.5, got beta={self._beta}")

        # Wald thresholds:
        # A = ln((1 - beta) / alpha) > 0  (rejection threshold)
        # B = ln(beta / (1 - alpha)) < 0  (acceptance threshold)
        with localcontext() as ctx:
            ctx.prec = 28
            a_val = math.log((1.0 - float(self._beta)) / float(self._alpha))
            b_val = math.log(float(self._beta) / (1.0 - float(self._alpha)))
            self._a_bound = Decimal(str(round(a_val, 8)))
            self._b_bound = Decimal(str(round(b_val, 8)))

        self._n = 0
        self._wins = 0
        self._llr = Decimal(0)
        self._decision = Decision.CONTINUE
        self._ever_rejected = False

    @property
    def p0(self) -> Decimal:
        return self._p0

    @property
    def p1(self) -> Decimal:
        return self._p1

    @property
    def alpha(self) -> Decimal:
        return self._alpha

    @property
    def beta(self) -> Decimal:
        return self._beta

    @property
    def a_bound(self) -> Decimal:
        return self._a_bound

    @property
    def b_bound(self) -> Decimal:
        return self._b_bound

    @property
    def llr(self) -> Decimal:
        return self._llr

    @property
    def n(self) -> int:
        return self._n

    @property
    def wins(self) -> int:
        return self._wins

    @property
    def decision(self) -> Decision:
        return self._decision

    @property
    def ever_rejected(self) -> bool:
        return self._ever_rejected

    def update(self, won: bool) -> Decision:
        """Update LLR with a single Bernoulli trial outcome until absorption."""
        if self._decision != Decision.CONTINUE:
            return self._decision

        self._n += 1
        if won:
            self._wins += 1
            log_term = math.log(float(self._p1) / float(self._p0))
        else:
            log_term = math.log((1.0 - float(self._p1)) / (1.0 - float(self._p0)))

        self._llr += Decimal(str(round(log_term, 8)))

        if self._llr <= self._b_bound:
            self._decision = Decision.ACCEPT_H0
        elif self._llr >= self._a_bound:
            self._decision = Decision.REJECT_H0
            self._ever_rejected = True
        else:
            self._decision = Decision.CONTINUE

        return self._decision

    def reset(self) -> None:
        """Reset trial statistics back to initial state."""
        self._n = 0
        self._wins = 0
        self._llr = Decimal(0)
        self._decision = Decision.CONTINUE
        self._ever_rejected = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize state for durable persistence."""
        return {
            "p0": str(self._p0),
            "p1": str(self._p1),
            "alpha": str(self._alpha),
            "beta": str(self._beta),
            "a_bound": str(self._a_bound),
            "b_bound": str(self._b_bound),
            "llr": str(self._llr),
            "n": self._n,
            "wins": self._wins,
            "decision": self._decision.value,
            "ever_rejected": self._ever_rejected,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SPRT:
        """Reconstruct SPRT instance from serialized state."""
        instance = cls(
            p0=Decimal(str(data["p0"])),
            p1=Decimal(str(data["p1"])),
            alpha=Decimal(str(data.get("alpha", "0.05"))),
            beta=Decimal(str(data.get("beta", "0.05"))),
        )
        instance._llr = Decimal(str(data["llr"]))
        instance._n = int(data["n"])
        instance._wins = int(data["wins"])
        instance._decision = Decision(data["decision"])
        instance._ever_rejected = bool(data.get("ever_rejected", False))
        return instance
