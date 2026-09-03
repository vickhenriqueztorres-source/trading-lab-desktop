"""Candidate grammar and deterministic enumeration for research (R-RES-3)."""

from __future__ import annotations

import itertools
import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from manifest_schema.families import FAMILY_COMPONENTS, Family
from primitives.base import Category, Indicator, ParamRange
from primitives.registry import REGISTRY, by_category

from strategy_lab.research.candidate import Candidate, ParamMap

# Incompatible pairs of primitives (R-RES-3)
INCOMPATIBLE: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"rsi_extreme", "quadrant_majority"}),
        frozenset({"bb_close_outside", "bb_width_ratio"}),
    }
)

# Standard timeframes and trading session hours
TIMEFRAMES: tuple[str, ...] = ("M1", "M5", "M15")
HOURS_SLOTS: tuple[tuple[int, int], ...] = (
    (0, 6),
    (6, 10),
    (10, 13),
    (13, 16),
    (16, 21),
    (0, 24),  # includes weekend full session
)

DEFAULT_ASSETS: tuple[str, ...] = ("EURUSD-OTC", "GBPUSD-OTC")

# Inverse lookup from (regime, trigger, confirm) -> Family name
_FAMILY_LOOKUP: dict[tuple[str, str, str], Family] = {
    components: family for family, components in FAMILY_COMPONENTS.items()
}


@dataclass(frozen=True)
class GrammarResult:
    candidates: list[Candidate]
    total_candidates: int
    seed: int


def is_compatible(regime: str, trigger: str, confirm: str) -> bool:
    """Return True if the trio does not contain any declared incompatible pair."""
    trio = {regime, trigger, confirm}
    return all(not pair.issubset(trio) for pair in INCOMPATIBLE)


def identify_family(regime: str, trigger: str, confirm: str) -> str:
    """Return canonical Family (F1..F5) or fallback identifier."""
    key = (regime, trigger, confirm)
    if key in _FAMILY_LOOKUP:
        return _FAMILY_LOOKUP[key]
    return f"F_{regime[:2]}_{trigger[:2]}_{confirm[:2]}".upper()


def generate_param_values(param_range: ParamRange) -> list[int | Decimal]:
    """Generate discrete values across a ParamRange, constrained for tractability."""
    # To avoid explosive candidate counts, take min, mid, and max if range is broad
    if param_range.kind == "int":
        curr_i = int(param_range.min)
        step_i = int(param_range.step)
        limit_i = int(param_range.max)
        span_i = limit_i - curr_i
        if span_i > 4 * step_i:
            mid_i = curr_i + ((span_i // 2) // step_i) * step_i
            return sorted(list({curr_i, mid_i, limit_i}))
        vals_i: list[int | Decimal] = []
        while curr_i <= limit_i:
            vals_i.append(curr_i)
            curr_i += step_i
        return vals_i or [param_range.min]
    else:
        assert isinstance(param_range.min, Decimal)
        assert isinstance(param_range.step, Decimal)
        assert isinstance(param_range.max, Decimal)
        curr_d = param_range.min
        step_d = param_range.step
        limit_d = param_range.max
        span_d = limit_d - curr_d
        if span_d > Decimal("4") * step_d:
            mid_d = curr_d + Decimal(int((span_d / Decimal("2")) / step_d)) * step_d
            return sorted(list({curr_d, mid_d, limit_d}))
        vals_d: list[int | Decimal] = []
        while curr_d <= limit_d:
            vals_d.append(curr_d)
            curr_d += step_d
        return vals_d or [param_range.min]


def generate_indicator_param_grid(indicator_cls: type[Indicator]) -> list[ParamMap]:
    """Return list of valid parameter mappings for an indicator from its param_spec."""
    spec = indicator_cls.param_spec
    if not spec:
        return [{}]

    param_names = list(spec.keys())
    value_lists = [generate_param_values(spec[name]) for name in param_names]

    grid: list[ParamMap] = []
    for combo in itertools.product(*value_lists):
        p_map = dict(zip(param_names, combo, strict=True))
        try:
            # Verify parameter domain constraints (e.g. short < med < long, support < resist)
            indicator_cls(**p_map)
            grid.append(p_map)
        except Exception:
            continue

    if not grid:
        # Fallback to default instance params if coarse grid missed constraints
        try:
            default_inst = indicator_cls()
            default_params = {
                name: getattr(default_inst, name)
                for name in param_names
                if hasattr(default_inst, name)
            }
            grid.append(default_params)
        except Exception:
            grid.append({})

    return grid


def enumerate_candidates(
    *,
    assets: Sequence[str] = DEFAULT_ASSETS,
    timeframes: Sequence[str] = TIMEFRAMES,
    hours_slots: Sequence[tuple[int, int]] = HOURS_SLOTS,
    max_candidates: int = 5000,
    seed: int = 1,
    include_non_standard_families: bool = False,
) -> GrammarResult:
    """Enumerate candidate strategies = 1 Regime x 1 Trigger x 1 Confirm x params.

    Guarantees:
    - Never generates 2 primitives from the same category.
    - Excludes INCOMPATIBLE pairs.
    - Deterministically caps candidates at max_candidates using seed.
    - Accurately tracks total_candidates for FDR.
    """
    regimes = by_category(Category.REGIME)
    triggers = by_category(Category.TRIGGER)
    confirms = by_category(Category.CONFIRM)

    # 1. Generate valid indicator trios
    trios: list[tuple[str, str, str]] = []
    if not include_non_standard_families:
        # Canonical families F1..F5
        for comp in FAMILY_COMPONENTS.values():
            if is_compatible(*comp):
                trios.append(comp)
    else:
        for r, t, c in itertools.product(regimes.keys(), triggers.keys(), confirms.keys()):
            if is_compatible(r, t, c):
                trios.append((r, t, c))

    all_candidates: list[Candidate] = []

    # 2. Build candidates across grid
    for reg_name, trig_name, conf_name in trios:
        fam = identify_family(reg_name, trig_name, conf_name)
        reg_cls = REGISTRY[reg_name]
        trig_cls = REGISTRY[trig_name]
        conf_cls = REGISTRY[conf_name]

        reg_grids = generate_indicator_param_grid(reg_cls)
        trig_grids = generate_indicator_param_grid(trig_cls)
        conf_grids = generate_indicator_param_grid(conf_cls)

        for asset in assets:
            for tf in timeframes:
                for hours in hours_slots:
                    for rp, tp, cp in itertools.product(reg_grids, trig_grids, conf_grids):
                        params: dict[str, ParamMap] = {
                            reg_name: rp,
                            trig_name: tp,
                            conf_name: cp,
                        }
                        cand = Candidate(
                            family=fam,
                            regime=reg_name,
                            trigger=trig_name,
                            confirm=conf_name,
                            params=params,
                            tf=tf,
                            hours=hours,
                            asset=asset,
                        )
                        all_candidates.append(cand)

    total_candidates = len(all_candidates)

    # 3. Deterministic sampling if count exceeds max_candidates
    if total_candidates > max_candidates:
        rng = random.Random(seed)
        sampled = rng.sample(all_candidates, max_candidates)
        # Sort sampled candidates by stable hash for deterministic execution order
        sampled.sort(key=lambda c: c.stable_hash())
        final_candidates = sampled
    else:
        all_candidates.sort(key=lambda c: c.stable_hash())
        final_candidates = all_candidates

    return GrammarResult(
        candidates=final_candidates,
        total_candidates=total_candidates,
        seed=seed,
    )
