"""Research core for Strategy Lab (R-RES-1, R-RES-4..6, R-RES-10 partial)."""

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.delay_penalty import apply_delay_penalty
from strategy_lab.research.outcome import settle
from strategy_lab.research.payout_lookup import PayoutLookup
from strategy_lab.research.portfolio_replay import (
    PortfolioDecisionReason,
    PortfolioOpportunity,
    PortfolioReplayConfig,
    PortfolioReplayResult,
    compare_portfolio_sizes,
    run_portfolio_replay,
)
from strategy_lab.research.portfolio_selection import (
    DevelopmentRecipe,
    FrozenPortfolioSelection,
    HoldoutEvaluation,
    HoldoutLedger,
    PortfolioManifestDraft,
    PortfolioSelectionConfig,
    build_manifest_draft,
    evaluate_frozen_holdout,
    save_selection_artifacts,
    select_portfolio,
)
from strategy_lab.research.replay_simulator import Trade, TradeLog, replay_candidate

__all__ = [
    "Candidate",
    "DevelopmentRecipe",
    "FrozenPortfolioSelection",
    "HoldoutEvaluation",
    "HoldoutLedger",
    "PayoutLookup",
    "PortfolioDecisionReason",
    "PortfolioOpportunity",
    "PortfolioManifestDraft",
    "PortfolioReplayConfig",
    "PortfolioReplayResult",
    "PortfolioSelectionConfig",
    "Trade",
    "TradeLog",
    "apply_delay_penalty",
    "compare_portfolio_sizes",
    "build_manifest_draft",
    "evaluate_frozen_holdout",
    "replay_candidate",
    "run_portfolio_replay",
    "save_selection_artifacts",
    "select_portfolio",
    "settle",
]
