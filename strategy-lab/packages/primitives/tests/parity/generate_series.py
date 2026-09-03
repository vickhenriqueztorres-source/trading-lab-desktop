"""Generate the immutable public 10k-candle conformance vector for R-PRIM-6."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

SEED = 20260902
COUNT = 10_000
START_TS = 1_800_000_000
SCALE = Decimal("0.0001")


def generate() -> list[dict[str, int | str]]:
    state = SEED
    close_ticks = 1_000_000
    rows: list[dict[str, int | str]] = []
    for index in range(COUNT):
        state = (1_103_515_245 * state + 12_345) % (2**31)
        cluster = 1 + (index // 250) % 5
        delta = ((state // 65_536) % 21 - 10) * cluster
        open_ticks = close_ticks
        close_ticks = max(1, open_ticks + delta)
        state = (1_103_515_245 * state + 12_345) % (2**31)
        upper = 1 + state % (3 * cluster + 1)
        state = (1_103_515_245 * state + 12_345) % (2**31)
        lower = 1 + state % (3 * cluster + 1)
        rows.append(
            {
                "ts": START_TS + index * 60,
                "o": format(Decimal(open_ticks) * SCALE, "f"),
                "h": format(Decimal(max(open_ticks, close_ticks) + upper) * SCALE, "f"),
                "l": format(Decimal(min(open_ticks, close_ticks) - lower) * SCALE, "f"),
                "c": format(Decimal(close_ticks) * SCALE, "f"),
                "tick_vol": 20 + state % 181,
            }
        )
    return rows


if __name__ == "__main__":
    destination = Path(__file__).with_name("series_10k.json")
    destination.write_text(
        json.dumps(generate(), ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
