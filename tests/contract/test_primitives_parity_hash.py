"""R-PRIM-6 / R-BOT-5: bot's local primitives produce the exact public canonical parity hash."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from apps.core.families.primitives import REGISTRY, Candle, Output
from apps.core.manifest_client import DEFAULT_PARITY_SHA256

SERIES_10K_PATH = (
    Path(__file__).resolve().parents[2]
    / "strategy-lab"
    / "packages"
    / "primitives"
    / "tests"
    / "parity"
    / "series_10k.json"
)


def decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def canonical_output(output: Output | None) -> dict[str, object] | None:
    if output is None:
        return None
    return {
        "direction": output.direction,
        "value": decimal_text(output.value),
        "meta": {key: decimal_text(output.meta[key]) for key in sorted(output.meta)},
    }


def compute_parity_hash() -> str:
    if not SERIES_10K_PATH.exists():
        pytest.skip(f"Parity series file not found at {SERIES_10K_PATH}")

    raw_rows = json.loads(SERIES_10K_PATH.read_text(encoding="utf-8"))
    indicators = {name: indicator_type() for name, indicator_type in REGISTRY.items()}
    digest = hashlib.sha256()

    for raw in raw_rows:
        candle = Candle(
            ts=raw["ts"],
            o=Decimal(raw["o"]),
            h=Decimal(raw["h"]),
            l=Decimal(raw["l"]),
            c=Decimal(raw["c"]),
            tick_vol=raw["tick_vol"],
        )
        for name in sorted(indicators):
            record = {
                "name": name,
                "ts": candle.ts,
                "output": canonical_output(indicators[name].update(candle)),
            }
            digest.update(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            digest.update(b"\n")
    return digest.hexdigest()


def test_bot_local_primitives_parity_hash() -> None:
    """Validate 100.00% numerical parity of bot's local primitives against specification."""
    expected_hash = DEFAULT_PARITY_SHA256.removeprefix("sha256:")
    actual_hash = compute_parity_hash()
    assert actual_hash == expected_hash, (
        f"Parity hash mismatch! Expected {expected_hash}, got {actual_hash}"
    )
