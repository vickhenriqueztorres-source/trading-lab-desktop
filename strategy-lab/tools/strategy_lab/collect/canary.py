"""IQ candle format canary (R-COL-2)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from primitives import Candle

from strategy_lab.collect.iq_client import LAB_ROOT, IQClientProtocol

CANARY = [
    ("EURUSD-OTC", 1700000040),
    ("EURUSD-OTC", 1700000100),
    ("EURUSD-OTC", 1700000160),
    ("EURUSD-OTC", 1700000220),
    ("EURUSD-OTC", 1700000280),
]
CANARY_FIXTURE = LAB_ROOT / "tests/fixtures/canary.json"


@dataclass(frozen=True)
class CanaryMismatch(Exception):
    reason: str
    asset: str
    ts: int

    def __str__(self) -> str:
        return "COL_CANARY_MISMATCH"


def run_canary(client: IQClientProtocol) -> None:
    expected = _load_expected()
    for asset, ts in CANARY:
        observed = client.fetch_candles(asset, 60, 1, ts + 60)
        if len(observed) != 1:
            raise CanaryMismatch("missing", asset, ts)
        if _canonical_candle(observed[0]) != expected.get((asset, ts)):
            raise CanaryMismatch("different", asset, ts)


def _load_expected() -> dict[tuple[str, int], dict[str, str | int]]:
    raw = json.loads(CANARY_FIXTURE.read_text(encoding="utf-8"), parse_float=Decimal)
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise RuntimeError("COL_CANARY_FIXTURE_INVALID")
    candles = raw.get("candles")
    if not isinstance(candles, list):
        raise RuntimeError("COL_CANARY_FIXTURE_INVALID")
    result: dict[tuple[str, int], dict[str, str | int]] = {}
    for row in candles:
        if not isinstance(row, dict):
            raise RuntimeError("COL_CANARY_FIXTURE_INVALID")
        asset = row["asset"]
        ts = row["ts"]
        if not isinstance(asset, str) or type(ts) is not int:
            raise RuntimeError("COL_CANARY_FIXTURE_INVALID")
        result[(asset, ts)] = {
            "o": str(row["o"]),
            "h": str(row["h"]),
            "l": str(row["l"]),
            "c": str(row["c"]),
            "tick_vol": int(row["tick_vol"]),
        }
    return result


def _canonical_candle(candle: Candle) -> dict[str, str | int]:
    return {
        "o": str(candle.o),
        "h": str(candle.h),
        "l": str(candle.l),
        "c": str(candle.c),
        "tick_vol": candle.tick_vol,
    }
