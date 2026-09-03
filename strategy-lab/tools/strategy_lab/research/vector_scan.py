"""Polars-backed triage scan; replay remains the only approval path (R-RES-5)."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from primitives import Candle
from primitives.base import Indicator
from primitives.registry import REGISTRY

from strategy_lab.research.candidate import Candidate
from strategy_lab.research.payout_lookup import PayoutLookup
from strategy_lab.research.replay_simulator import replay_candidate


def vector_scan_candidate(
    candidate: Candidate,
    candles: list[Candle],
    payout_lookup: PayoutLookup,
    *,
    registry: Mapping[str, type[Indicator]] = REGISTRY,
) -> Any:
    """Return triage signals as a Polars DataFrame.

    The replay simulator remains the only approval path. The Polars path currently covers the
    canonical range-rejection candidate; other combinations fail over to replay-equivalent
    timestamps so they can be ranked only after incremental replay.
    """
    if (
        candidate.regime == "session_window"
        and candidate.trigger == "range_break"
        and candidate.confirm == "candle_rejection"
    ):
        return _scan_session_range_rejection(candidate, candles, payout_lookup)
    pl = importlib.import_module("polars")
    log = replay_candidate(candidate, candles, payout_lookup, registry=registry)
    return pl.DataFrame(
        [
            {
                "ts": trade.ts,
                "direction": trade.direction,
                "payout_return_ratio": str(trade.payout_return_ratio),
            }
            for trade in log.trades
        ]
    )


def _scan_session_range_rejection(
    candidate: Candidate,
    candles: list[Candle],
    payout_lookup: PayoutLookup,
) -> Any:
    pl = importlib.import_module("polars")
    scale = Decimal("100000000")
    range_params = candidate.params_for("range_break")
    rejection_params = candidate.params_for("candle_rejection")
    session_params = candidate.params_for("session_window")
    length = int(range_params.get("length", 20))
    max_body_ratio = _scaled_ratio(rejection_params.get("max_body_ratio", Decimal("0.35")), scale)
    min_wick_ratio = _scaled_ratio(rejection_params.get("min_wick_ratio", Decimal("0.5")), scale)
    start_minute = int(session_params.get("start_minute", 0))
    end_minute = int(session_params.get("end_minute", 360))
    rows = [
        {
            "ts": candle.ts,
            "o": _scaled_price(candle.o, scale),
            "h": _scaled_price(candle.h, scale),
            "l": _scaled_price(candle.l, scale),
            "c": _scaled_price(candle.c, scale),
        }
        for candle in sorted(candles, key=lambda item: item.ts)
    ]
    if not rows:
        return pl.DataFrame([])
    frame = pl.DataFrame(rows).with_columns(
        pl.col("h").rolling_max(window_size=length).shift(1).alias("range_upper"),
        pl.col("l").rolling_min(window_size=length).shift(1).alias("range_lower"),
        (pl.col("ts") % 86400 // 60).alias("minute_utc"),
        (pl.col("h") - pl.col("l")).alias("span"),
        (pl.col("c") - pl.col("o")).abs().alias("body"),
        (pl.min_horizontal("o", "c") - pl.col("l")).alias("lower_wick"),
        (pl.col("h") - pl.max_horizontal("o", "c")).alias("upper_wick"),
    )
    active_expr = (
        (pl.col("minute_utc") >= start_minute) & (pl.col("minute_utc") < end_minute)
        if start_minute < end_minute
        else (pl.col("minute_utc") >= start_minute) | (pl.col("minute_utc") < end_minute)
    )
    frame = frame.with_columns(
        active_expr.alias("regime_active"),
        pl.when(pl.col("c") > pl.col("range_upper"))
        .then(pl.lit("call"))
        .when(pl.col("c") < pl.col("range_lower"))
        .then(pl.lit("put"))
        .otherwise(pl.lit("none"))
        .alias("trigger_direction"),
        pl.when(
            (pl.col("span") > 0)
            & (pl.col("body") * int(scale) <= pl.col("span") * max_body_ratio)
            & (pl.col("lower_wick") * int(scale) >= pl.col("span") * min_wick_ratio)
            & (pl.col("lower_wick") > pl.col("upper_wick"))
        )
        .then(pl.lit("call"))
        .when(
            (pl.col("span") > 0)
            & (pl.col("body") * int(scale) <= pl.col("span") * max_body_ratio)
            & (pl.col("upper_wick") * int(scale) >= pl.col("span") * min_wick_ratio)
            & (pl.col("upper_wick") > pl.col("lower_wick"))
        )
        .then(pl.lit("put"))
        .otherwise(pl.lit("none"))
        .alias("confirm_direction"),
    )
    frame = frame.with_columns(
        pl.when(
            pl.col("regime_active")
            & (pl.col("trigger_direction") == pl.col("confirm_direction"))
            & (pl.col("trigger_direction") != "none")
        )
        .then(pl.col("trigger_direction"))
        .otherwise(pl.lit("none"))
        .alias("direction")
    )
    signal_rows = frame.filter(pl.col("direction") != "none").select("ts", "direction").to_dicts()
    return pl.DataFrame(
        [
            {
                "ts": int(row["ts"]),
                "direction": str(row["direction"]),
                "payout_return_ratio": str(payout),
            }
            for row in signal_rows
            if (payout := payout_lookup.payout(candidate.asset, int(row["ts"]))) is not None
        ]
    )


def _scaled_price(value: Decimal, scale: Decimal) -> int:
    return int(value * scale)


def _scaled_ratio(value: object, scale: Decimal) -> int:
    return int(Decimal(str(value)) * scale)
