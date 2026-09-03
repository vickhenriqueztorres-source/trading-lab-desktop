"""R-MAN-3: stable wire names mapped to P01 ranges, without changing P01 math."""

from typing import Literal

from primitives.base import ParamRange, decimal_range
from primitives.registry import REGISTRY

type Family = Literal["F1", "F2", "F3", "F4", "F5"]

FAMILY_COMPONENTS: dict[Family, tuple[str, str, str]] = {
    "F1": ("adx", "bb_close_outside", "rsi_extreme"),
    "F2": ("ema_alignment", "ema_pullback", "candle_rejection"),
    "F3": ("session_window", "level_touch", "candle_rejection"),
    "F4": ("bb_width_ratio", "range_break", "tick_volume_ratio"),
    "F5": ("session_window", "quadrant_majority", "rsi_extreme"),
}

FAMILY_BINDINGS: dict[Family, dict[str, tuple[str, str]]] = {
    "F1": {
        "adx_len": ("adx", "period"),
        "bb_len": ("bb_close_outside", "length"),
        "bb_k": ("bb_close_outside", "k"),
        "rsi_len": ("rsi_extreme", "period"),
        "rsi_lo": ("rsi_extreme", "lower"),
        "rsi_hi": ("rsi_extreme", "upper"),
    },
    "F2": {
        "ema_short": ("ema_alignment", "short"),
        "ema_medium": ("ema_alignment", "medium"),
        "ema_long": ("ema_alignment", "long"),
        "pullback_len": ("ema_pullback", "period"),
        "pullback_tolerance": ("ema_pullback", "tolerance"),
        "body_max": ("candle_rejection", "max_body_ratio"),
        "wick_min": ("candle_rejection", "min_wick_ratio"),
    },
    "F3": {
        "level_support": ("level_touch", "support"),
        "level_resistance": ("level_touch", "resistance"),
        "level_tolerance": ("level_touch", "tolerance"),
        "body_max": ("candle_rejection", "max_body_ratio"),
        "wick_min": ("candle_rejection", "min_wick_ratio"),
    },
    "F4": {
        "bb_len": ("bb_width_ratio", "length"),
        "bb_k": ("bb_width_ratio", "k"),
        "width_median_len": ("bb_width_ratio", "median_length"),
        "break_len": ("range_break", "length"),
        "volume_len": ("tick_volume_ratio", "length"),
        "volume_min": ("tick_volume_ratio", "minimum_ratio"),
    },
    "F5": {
        "quadrant_window": ("quadrant_majority", "window"),
        "rsi_len": ("rsi_extreme", "period"),
        "rsi_lo": ("rsi_extreme", "lower"),
        "rsi_hi": ("rsi_extreme", "upper"),
    },
}

# Composition gates, NOT constructor parameters. Their ownership is explicit:
# adx_max bounds ADX output; width_ratio_max bounds the BB width ratio output.
# All constructor parameter ranges below come directly from the P01 registry.
FAMILY_GATES: dict[Family, dict[str, ParamRange]] = {
    "F1": {"adx_max": decimal_range("0", "100", "1")},
    "F2": {},
    "F3": {},
    "F4": {"width_ratio_max": decimal_range("0.1", "1", "0.1")},
    "F5": {},
}
FAMILY_SPECS: dict[Family, dict[str, ParamRange]] = {
    family: {
        **{wire: REGISTRY[name].param_spec[param] for wire, (name, param) in bindings.items()},
        **FAMILY_GATES[family],
    }
    for family, bindings in FAMILY_BINDINGS.items()
}
FAMILY_RELATIONS: dict[Family, tuple[tuple[str, str], ...]] = {
    "F1": (("rsi_lo", "rsi_hi"),),
    "F2": (("ema_short", "ema_medium"), ("ema_medium", "ema_long")),
    "F3": (("level_support", "level_resistance"),),
    "F4": (),
    "F5": (("rsi_lo", "rsi_hi"),),
}
