"""Sequential Probability Ratio Test (SPRT) reference implementation (R-PUB-5, R-BOT-7)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, getcontext, localcontext
from enum import StrEnum

getcontext().prec = 28


class SprtDecision(StrEnum):
    CONTINUE = "continue"
    ACCEPT_H0 = "accept_h0"  # Edge confirmed, keep / promote
    REJECT_H0 = "reject_h0"  # Edge lost, reject / demote to observation


@dataclass(frozen=True)
class SprtResult:
    decision: SprtDecision
    llr: Decimal
    lower_bound: Decimal
    upper_bound: Decimal
    n_samples: int
    wins: int


class WaldSprt:
    """Wald's Sequential Probability Ratio Test for binary Bernoulli trials.

    H0: p = p_0 (baseline rate / wilson_lower)
    H1: p = p_1 (unacceptable rate / p_min)
    alpha = type I error (0.05)
    beta = type II error (0.05)
    """

    def __init__(
        self,
        p_0: Decimal | float,
        p_1: Decimal | float,
        alpha: Decimal | float = 0.05,
        beta: Decimal | float = 0.05,
    ) -> None:
        self.p_0 = Decimal(str(p_0))
        self.p_1 = Decimal(str(p_1))
        self.alpha = Decimal(str(alpha))
        self.beta = Decimal(str(beta))

        if not (Decimal(0) < self.p_1 < self.p_0 < Decimal(1)):
            raise ValueError(f"SPRT requires 0 < p_1 < p_0 < 1, got p_1={self.p_1}, p_0={self.p_0}")

        # Wald thresholds:
        # A = ln(beta / (1 - alpha))  < 0
        # B = ln((1 - beta) / alpha)  > 0
        with localcontext() as ctx:
            ctx.prec = 28
            a_val = math.log(float(self.beta) / (1.0 - float(self.alpha)))
            b_val = math.log((1.0 - float(self.beta)) / float(self.alpha))
            self.lower_bound = Decimal(str(round(a_val, 8)))
            self.upper_bound = Decimal(str(round(b_val, 8)))

        self._n = 0
        self._wins = 0
        self._llr = Decimal(0)
        self._decision = SprtDecision.CONTINUE
        self._ever_rejected = False

    @property
    def llr(self) -> Decimal:
        return self._llr

    def update(self, won: bool) -> SprtResult:
        """Update LLR with a single Bernoulli trial outcome until absorption."""
        if self._decision != SprtDecision.CONTINUE:
            return SprtResult(
                decision=self._decision,
                llr=self._llr,
                lower_bound=self.lower_bound,
                upper_bound=self.upper_bound,
                n_samples=self._n,
                wins=self._wins,
            )

        self._n += 1
        if won:
            self._wins += 1
            log_term = math.log(float(self.p_1) / float(self.p_0))
        else:
            log_term = math.log((1.0 - float(self.p_1)) / (1.0 - float(self.p_0)))

        self._llr += Decimal(str(round(log_term, 8)))

        if self._llr <= self.lower_bound:
            self._decision = SprtDecision.ACCEPT_H0
        elif self._llr >= self.upper_bound:
            self._decision = SprtDecision.REJECT_H0
            self._ever_rejected = True
        else:
            self._decision = SprtDecision.CONTINUE

        return SprtResult(
            decision=self._decision,
            llr=self._llr,
            lower_bound=self.lower_bound,
            upper_bound=self.upper_bound,
            n_samples=self._n,
            wins=self._wins,
        )

    def evaluate_series(self, outcomes: Sequence[bool]) -> SprtResult:
        """Evaluate a sequence of trials from the beginning."""
        self._n = 0
        self._wins = 0
        self._llr = Decimal(0)
        self._decision = SprtDecision.CONTINUE
        self._ever_rejected = False
        for won in outcomes:
            self.update(won)
        return SprtResult(
            decision=self._decision,
            llr=self._llr,
            lower_bound=self.lower_bound,
            upper_bound=self.upper_bound,
            n_samples=self._n,
            wins=self._wins,
        )

    def is_eligible_for_promotion(
        self,
        outcomes: Sequence[bool],
        days: int = 0,
        min_ops: int = 200,
        min_days: int = 30,
    ) -> bool:
        """R-PUB-5: promote observation -> approved only if >= 200 ops or >= 30 days."""
        if len(outcomes) < min_ops and days < min_days:
            return False

        self.evaluate_series(outcomes)
        return not self._ever_rejected and self._decision != SprtDecision.REJECT_H0
