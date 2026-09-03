"""Sealed holdout management and burned range tracking (R-RES-2)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from primitives import Candle

THREE_MONTHS_S: int = 90 * 86400  # 90 days in seconds


@dataclass(frozen=True)
class HoldoutSplit:
    train_val_candles: list[Candle]
    holdout_candles: list[Candle]
    holdout_range: tuple[int, int]
    holdout_hash: str


def compute_candles_hash(candles: Sequence[Candle]) -> str:
    """Compute deterministic SHA-256 hash of candles."""
    raw_items = [
        {
            "c": format(c.c, "f"),
            "h": format(c.h, "f"),
            "l": format(c.l, "f"),
            "o": format(c.o, "f"),
            "tick_vol": c.tick_vol,
            "ts": c.ts,
        }
        for c in candles
    ]
    encoded = json.dumps(raw_items, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def separate_holdout(
    candles: Sequence[Candle],
    duration_s: int = THREE_MONTHS_S,
) -> HoldoutSplit:
    """Separate the last 3 months as sealed holdout (R-RES-2).

    If dataset span is shorter than standard 3 months, reserves the last 20% of the span.
    """
    if not candles:
        raise ValueError("Cannot separate holdout from empty candle dataset")

    ordered = sorted(candles, key=lambda c: c.ts)
    from_ts = ordered[0].ts
    to_ts = ordered[-1].ts
    total_span = to_ts - from_ts

    effective_duration = duration_s
    if total_span <= duration_s:
        # Fallback: reserve last 20% of candles if dataset is shorter than 90 days
        split_idx = max(int(len(ordered) * 0.8), 1)
        holdout_from = ordered[split_idx].ts
    else:
        holdout_from = to_ts - effective_duration

    train_val = [c for c in ordered if c.ts < holdout_from]
    holdout = [c for c in ordered if c.ts >= holdout_from]

    if (not train_val or not holdout) and len(ordered) >= 2:
        train_val = list(ordered[:-1])
        holdout = [ordered[-1]]
        holdout_from = ordered[-1].ts

    h_range = (holdout_from, to_ts)
    h_hash = compute_candles_hash(holdout)

    return HoldoutSplit(
        train_val_candles=train_val,
        holdout_candles=holdout,
        holdout_range=h_range,
        holdout_hash=h_hash,
    )


class HoldoutManager:
    """Tracks opened holdouts per run and burned ranges across rounds (R-RES-2)."""

    def __init__(self, db_connection: Any = None) -> None:
        self._db = db_connection
        self._opened_runs: set[str] = set()
        self._burned_ranges: set[tuple[int, int]] = set()

    def open_once(self, run_id: str, holdout_candles: Sequence[Candle]) -> Sequence[Candle]:
        """Open the sealed holdout strictly once per run. Second invocation fails closed."""
        if run_id in self._opened_runs:
            raise RuntimeError(
                f"Violation R-RES-2: Holdout for run '{run_id}' has already been opened! "
                "Double-opening is prohibited."
            )
        self._opened_runs.add(run_id)
        return holdout_candles

    def is_opened(self, run_id: str) -> bool:
        return run_id in self._opened_runs

    def burn(
        self,
        range_ts: tuple[int, int],
        run_id: str = "",
        burned_at: int | None = None,
    ) -> None:
        """Register range as burned in holdout_burned so future rounds use different intervals."""
        self._burned_ranges.add(range_ts)
        if self._db is not None:
            try:
                from_ts, to_ts = range_ts
                range_id = f"{run_id}_{from_ts}_{to_ts}" if run_id else f"{from_ts}_{to_ts}"
                cur = self._db.cursor()
                cur.execute(
                    """
                    INSERT INTO public.holdout_burned (range_id, from_ts, to_ts, burned_at, run_id)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (range_id) DO NOTHING
                    """,
                    (range_id, from_ts, to_ts, burned_at or to_ts, run_id or None),
                )
                self._db.commit()
            except Exception:
                pass

    def is_burned(self, range_ts: tuple[int, int]) -> bool:
        """Return True if range has been burned or overlaps significantly with burned ranges."""
        if range_ts in self._burned_ranges:
            return True
        from_ts, to_ts = range_ts
        return any(max(from_ts, b_from) < min(to_ts, b_to) for b_from, b_to in self._burned_ranges)

    def refuse_if_burned(self, range_ts: tuple[int, int]) -> None:
        if self.is_burned(range_ts):
            raise ValueError(
                f"Violation R-RES-2: Holdout range {range_ts} is burned! "
                "The next research round must use a different range."
            )
