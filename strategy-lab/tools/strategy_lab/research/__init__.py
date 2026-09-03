"""Research core for Strategy Lab (R-RES-1, R-RES-4..6, R-RES-10 partial)."""

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.delay_penalty import apply_delay_penalty
from strategy_lab.research.outcome import settle
from strategy_lab.research.payout_lookup import PayoutLookup
from strategy_lab.research.replay_simulator import Trade, TradeLog, replay_candidate

__all__ = [
    "Candidate",
    "PayoutLookup",
    "Trade",
    "TradeLog",
    "apply_delay_penalty",
    "replay_candidate",
    "settle",
]
