"""Statistical research gates package (R-RES-7, R-RES-8, R-RES-10)."""

from __future__ import annotations

from strategy_lab.research.gates.approve import ApprovalResult, approve_candidate
from strategy_lab.research.gates.multiple_testing import (
    FDRResult,
    PermutationResult,
    benjamini_hochberg,
    binomial_survival_p_value,
    fdr_candidate_check,
    permutation_test,
)
from strategy_lab.research.gates.neighborhood import (
    NeighborhoodResult,
    evaluate_neighborhood,
    generate_neighbors,
)
from strategy_lab.research.gates.pbo import (
    PBOResult,
    compute_pbo_from_matrix,
    evaluate_pbo,
)
from strategy_lab.research.gates.pipeline import GateResult, run_pipeline
from strategy_lab.research.gates.walk_forward import (
    StabilityResult,
    WalkForwardWindow,
    evaluate_stability,
    generate_anchored_slices,
    partition_trades_into_windows,
)
from strategy_lab.research.gates.wilson import wilson_lower, wilson_lower_p

__all__ = [
    "ApprovalResult",
    "FDRResult",
    "GateResult",
    "NeighborhoodResult",
    "PBOResult",
    "PermutationResult",
    "StabilityResult",
    "WalkForwardWindow",
    "approve_candidate",
    "benjamini_hochberg",
    "binomial_survival_p_value",
    "compute_pbo_from_matrix",
    "evaluate_neighborhood",
    "evaluate_pbo",
    "evaluate_stability",
    "fdr_candidate_check",
    "generate_anchored_slices",
    "generate_neighbors",
    "partition_trades_into_windows",
    "permutation_test",
    "run_pipeline",
    "wilson_lower",
    "wilson_lower_p",
]
