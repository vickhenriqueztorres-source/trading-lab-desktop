"""Candidate grammar and deterministic enumeration for research (R-RES-3)."""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from manifest_schema.families import (
    FAMILY_BINDINGS,
    FAMILY_COMPONENTS,
    Family,
    family_warmup_required,
)
from primitives.base import Category, Indicator, ParamRange
from primitives.registry import REGISTRY, by_category

from strategy_lab.research.candidate import Candidate, ParamMap

DEFAULT_TRIAL_BUDGET = 500
DEFAULT_MAX_WARMUP_CANDLES = 10_000
EXECUTOR_SUPPORTED_FAMILIES: frozenset[str] = frozenset(FAMILY_COMPONENTS)
EXECUTOR_SUPPORTED_TIMEFRAMES: frozenset[str] = frozenset({"M1", "M5", "M15"})
EXECUTOR_RESEARCH_PRODUCT = "binary_option"

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
_CANONICAL_FAMILY_TRIOS: frozenset[frozenset[str]] = frozenset(
    frozenset(components) for components in FAMILY_COMPONENTS.values()
)

type LevelProfile = dict[str, Decimal]


@dataclass(frozen=True)
class GrammarResult:
    candidates: list[Candidate]
    total_candidates: int
    seed: int
    theoretical_candidates: int = 0
    eligible_candidates: int = 0
    sampled_candidates: int = 0
    trial_budget: int = DEFAULT_TRIAL_BUDGET
    discarded_by_reason: dict[str, int] | None = None

    def audit_report(self) -> dict[str, object]:
        """Return deterministic CAT-07 accounting for logs/reports."""
        return {
            "product": EXECUTOR_RESEARCH_PRODUCT,
            "seed": self.seed,
            "trial_budget": self.trial_budget,
            "theoretical_candidates": self.theoretical_candidates or self.total_candidates,
            "eligible_candidates": self.eligible_candidates or self.total_candidates,
            "sampled_candidates": self.sampled_candidates or len(self.candidates),
            "discarded_by_reason": self.discarded_by_reason or {},
            "diversity": {
                "families": _counts(candidate.family for candidate in self.candidates),
                "assets": _counts(candidate.asset for candidate in self.candidates),
                "timeframes": _counts(candidate.tf for candidate in self.candidates),
                "hours": _counts(
                    f"{candidate.hours[0]:02d}-{candidate.hours[1]:02d}"
                    for candidate in self.candidates
                ),
            },
        }


@dataclass(frozen=True)
class ExecutorCapabilities:
    """Public executor budget consumed by CAT-07 before materializing candidates."""

    families: frozenset[str] = EXECUTOR_SUPPORTED_FAMILIES
    timeframes: frozenset[str] = EXECUTOR_SUPPORTED_TIMEFRAMES
    max_warmup_candles: int = DEFAULT_MAX_WARMUP_CANDLES
    tick_volume: bool = True
    max_trials_per_experiment: int = DEFAULT_TRIAL_BUDGET
    supported_assets: frozenset[str] | None = None


DEFAULT_EXECUTOR_CAPABILITIES = ExecutorCapabilities()


def is_compatible(regime: str, trigger: str, confirm: str) -> bool:
    """Return True if the trio does not contain any declared incompatible pair."""
    trio = {regime, trigger, confirm}
    if frozenset(trio) in _CANONICAL_FAMILY_TRIOS:
        return True
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
        step_count = max((limit_i - curr_i) // step_i, 0)
        last_i = curr_i + step_count * step_i
        if step_count > 4:
            mid_i = curr_i + (step_count // 2) * step_i
            return sorted(list({curr_i, mid_i, last_i}))
        vals_i: list[int | Decimal] = []
        while curr_i <= last_i:
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
        step_count_d = ((limit_d - curr_d) / step_d).to_integral_value(rounding=ROUND_FLOOR)
        step_count = max(int(step_count_d), 0)
        last_d = curr_d + Decimal(step_count) * step_d
        if step_count > 4:
            mid_d = curr_d + Decimal(step_count // 2) * step_d
            return sorted(list({curr_d, mid_d, last_d}))
        vals_d: list[int | Decimal] = []
        while curr_d <= last_d:
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
    max_candidates: int = DEFAULT_TRIAL_BUDGET,
    seed: int = 1,
    include_non_standard_families: bool = False,
    executor_capabilities: ExecutorCapabilities = DEFAULT_EXECUTOR_CAPABILITIES,
    level_profiles: dict[str, Sequence[LevelProfile]] | None = None,
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

    trial_budget = min(max_candidates, executor_capabilities.max_trials_per_experiment)
    discarded: dict[str, int] = {}

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

    selected: list[tuple[int, str, Candidate]] = []
    selected_hashes: set[str] = set()
    seen_hashes: set[str] = set()
    theoretical_candidates = 0
    eligible_candidates = 0

    # 2. Build candidates across grid
    for reg_name, trig_name, conf_name in trios:
        fam = identify_family(reg_name, trig_name, conf_name)
        if fam not in executor_capabilities.families:
            _discard(discarded, "FAMILY_UNSUPPORTED")
            continue
        reg_cls = REGISTRY[reg_name]
        trig_cls = REGISTRY[trig_name]
        conf_cls = REGISTRY[conf_name]

        reg_grids = generate_indicator_param_grid(reg_cls)
        trig_grids = _trigger_grid_for_asset_independent(trig_name, trig_cls)
        conf_grids = generate_indicator_param_grid(conf_cls)

        for asset in assets:
            if (
                executor_capabilities.supported_assets is not None
                and asset not in executor_capabilities.supported_assets
            ):
                _discard(discarded, "ASSET_UNSUPPORTED")
                continue
            asset_trigger_grids = _trigger_grids_for_asset(
                trig_name,
                trig_grids,
                asset=asset,
                level_profiles=level_profiles,
            )
            if not asset_trigger_grids:
                _discard(discarded, "ABSOLUTE_LEVEL_PROFILE_REQUIRED")
                continue
            for tf in timeframes:
                if tf not in executor_capabilities.timeframes:
                    _discard(discarded, "TIMEFRAME_UNSUPPORTED")
                    continue
                for hours in hours_slots:
                    for rp, tp, cp in itertools.product(reg_grids, asset_trigger_grids, conf_grids):
                        theoretical_candidates += 1
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
                        cand_hash = cand.stable_hash()
                        if cand_hash in seen_hashes:
                            _discard(discarded, "DUPLICATE_CANDIDATE")
                            continue
                        seen_hashes.add(cand_hash)
                        reason = _capability_rejection(cand, executor_capabilities)
                        if reason is not None:
                            _discard(discarded, reason)
                            continue
                        eligible_candidates += 1
                        if cand_hash in selected_hashes:
                            continue
                        sample_key = _sample_key(seed, cand_hash)
                        if len(selected) < trial_budget:
                            selected.append((sample_key, cand_hash, cand))
                            selected_hashes.add(cand_hash)
                        else:
                            worst_idx, worst = max(
                                enumerate(selected),
                                key=lambda item: (item[1][0], item[1][1]),
                            )
                            if (sample_key, cand_hash) < (worst[0], worst[1]):
                                selected_hashes.remove(worst[1])
                                selected[worst_idx] = (sample_key, cand_hash, cand)
                                selected_hashes.add(cand_hash)

    selected.sort(key=lambda item: item[1])
    final_candidates = [candidate for _sample_key_item, _candidate_hash, candidate in selected]

    return GrammarResult(
        candidates=final_candidates,
        total_candidates=eligible_candidates,
        seed=seed,
        theoretical_candidates=theoretical_candidates,
        eligible_candidates=eligible_candidates,
        sampled_candidates=len(final_candidates),
        trial_budget=trial_budget,
        discarded_by_reason=discarded,
    )


def _discard(discarded: dict[str, int], reason: str) -> None:
    discarded[reason] = discarded.get(reason, 0) + 1


def _counts(items: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        result[item] = result.get(item, 0) + 1
    return result


def _sample_key(seed: int, candidate_hash: str) -> int:
    rng = random.Random(f"{seed}:{candidate_hash}")
    return rng.getrandbits(128)


def _trigger_grid_for_asset_independent(
    trigger_name: str,
    trigger_cls: type[Indicator],
) -> list[ParamMap]:
    if trigger_name == "level_touch":
        return []
    return generate_indicator_param_grid(trigger_cls)


def _trigger_grids_for_asset(
    trigger_name: str,
    trigger_grids: list[ParamMap],
    *,
    asset: str,
    level_profiles: dict[str, Sequence[LevelProfile]] | None,
) -> list[ParamMap]:
    if trigger_name != "level_touch":
        return trigger_grids
    profiles = () if level_profiles is None else level_profiles.get(asset, ())
    return [
        {
            "support": profile["support"],
            "resistance": profile["resistance"],
            "tolerance": profile["tolerance"],
        }
        for profile in profiles
        if profile["support"] > 0
        and profile["resistance"] > profile["support"]
        and profile["tolerance"] >= 0
    ]


def _capability_rejection(
    candidate: Candidate,
    executor_capabilities: ExecutorCapabilities,
) -> str | None:
    if _candidate_requires_tick_volume(candidate) and not executor_capabilities.tick_volume:
        return "TICK_VOLUME_UNAVAILABLE"
    if (
        executor_capabilities.max_warmup_candles < DEFAULT_MAX_WARMUP_CANDLES
        and _candidate_warmup_required(candidate) > executor_capabilities.max_warmup_candles
    ):
        return "WARMUP_CAPACITY_EXCEEDED"
    return None


def _candidate_requires_tick_volume(candidate: Candidate) -> bool:
    return "tick_volume_ratio" in {candidate.regime, candidate.trigger, candidate.confirm}


def _candidate_warmup_required(candidate: Candidate) -> int:
    family = candidate.family
    if family not in FAMILY_BINDINGS:
        return DEFAULT_MAX_WARMUP_CANDLES + 1
    wire_params: dict[str, str] = {}
    for wire_name, (primitive_name, primitive_param) in FAMILY_BINDINGS[family].items():
        raw = candidate.params.get(primitive_name, {}).get(primitive_param)
        if raw is None:
            return DEFAULT_MAX_WARMUP_CANDLES + 1
        wire_params[wire_name] = format(raw, "f") if isinstance(raw, Decimal) else str(raw)
    return family_warmup_required(family, wire_params, candidate.hours)
