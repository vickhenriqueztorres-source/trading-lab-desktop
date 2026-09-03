"""Neighborhood parameter stability gate (R-RES-7)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, getcontext

from primitives import Candle
from primitives.base import Indicator, ParamRange
from primitives.registry import REGISTRY

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.payout_lookup import PayoutLookup
from strategy_lab.research.replay_simulator import replay_candidate

getcontext().prec = 28

DEFAULT_NEIGHBORHOOD_MARGIN = Decimal("0.015")  # +1.5 pp


@dataclass(frozen=True)
class NeighborhoodResult:
    passed: bool
    median_p_hat: Decimal
    neighbor_p_hats: tuple[Decimal, ...]
    required_threshold: Decimal
    reason: str = ""


def generate_neighbors(
    candidate: Candidate,
    *,
    registry: Mapping[str, type[Indicator]] = REGISTRY,
    perturbation_pct: Decimal = Decimal("0.15"),
) -> list[Candidate]:
    """Generate neighbor candidates by perturbing each parameter +/-15% on its param_spec grid."""
    neighbors: list[Candidate] = []

    for indicator_name, param_dict in candidate.params.items():
        if indicator_name not in registry:
            continue
        indicator_cls = registry[indicator_name]
        spec_map = indicator_cls.param_spec

        for param_name, current_val in param_dict.items():
            if param_name not in spec_map:
                continue
            spec: ParamRange = spec_map[param_name]
            perturbed_vals = _perturb_parameter(current_val, spec, perturbation_pct)

            for new_val in perturbed_vals:
                new_params: dict[str, dict[str, Decimal | int | str]] = {
                    ind: dict(p_dict) for ind, p_dict in candidate.params.items()
                }
                new_params[indicator_name][param_name] = new_val
                neighbors.append(
                    Candidate(
                        family=candidate.family,
                        regime=candidate.regime,
                        trigger=candidate.trigger,
                        confirm=candidate.confirm,
                        params=new_params,
                        tf=candidate.tf,
                        hours=candidate.hours,
                        asset=candidate.asset,
                    )
                )

    return neighbors


def evaluate_neighborhood(
    candidate: Candidate,
    candles: list[Candle],
    payout_lookup: PayoutLookup,
    p_min: Decimal,
    *,
    margin: Decimal = DEFAULT_NEIGHBORHOOD_MARGIN,
    registry: Mapping[str, type[Indicator]] = REGISTRY,
) -> NeighborhoodResult:
    """Replay candidate neighbors and require median(p_hat_neighbors) >= p_min + 1.5 pp."""
    neighbors = generate_neighbors(candidate, registry=registry)
    threshold = p_min + margin

    if not neighbors:
        # If no parameters could be perturbed, evaluate the candidate itself
        log = replay_candidate(candidate, candles, payout_lookup, registry=registry)
        passed = log.p_hat >= threshold
        return NeighborhoodResult(
            passed=passed,
            median_p_hat=log.p_hat,
            neighbor_p_hats=(log.p_hat,),
            required_threshold=threshold,
            reason="" if passed else "MEDIAN_BELOW_THRESHOLD",
        )

    p_hats: list[Decimal] = []
    for neighbor in neighbors:
        log = replay_candidate(neighbor, candles, payout_lookup, registry=registry)
        p_hats.append(log.p_hat)

    sorted_p_hats = sorted(p_hats)
    k = len(sorted_p_hats)
    if k % 2 == 1:
        median_p_hat = sorted_p_hats[k // 2]
    else:
        median_p_hat = (sorted_p_hats[k // 2 - 1] + sorted_p_hats[k // 2]) / Decimal("2")

    passed = median_p_hat >= threshold
    return NeighborhoodResult(
        passed=passed,
        median_p_hat=median_p_hat,
        neighbor_p_hats=tuple(sorted_p_hats),
        required_threshold=threshold,
        reason="" if passed else "MEDIAN_BELOW_THRESHOLD",
    )


def _perturb_parameter(
    value: Decimal | int | str,
    spec: ParamRange,
    perturbation_pct: Decimal,
) -> list[Decimal | int]:
    candidates: list[Decimal | int] = []
    min_val = Decimal(str(spec.min))
    max_val = Decimal(str(spec.max))
    step = Decimal(str(spec.step))
    curr = Decimal(str(value))

    # Calculate lower and upper perturbations
    delta = curr * perturbation_pct
    raw_low = curr - delta
    raw_high = curr + delta

    for raw in (raw_low, raw_high):
        # Snap to nearest step on grid: min + round((raw - min) / step) * step
        steps_from_min = ((raw - min_val) / step).quantize(Decimal("1"))
        snapped = min_val + steps_from_min * step

        # If snapped equals current, force at least 1 step change if possible
        if snapped == curr:
            if raw < curr and curr - step >= min_val:
                snapped = curr - step
            elif raw > curr and curr + step <= max_val:
                snapped = curr + step

        # Clamp to bounds
        clamped = max(min_val, min(max_val, snapped))
        if clamped != curr:
            if spec.kind == "int":
                candidates.append(int(clamped))
            else:
                candidates.append(clamped)

    return list(dict.fromkeys(candidates))  # Preserve order, unique
