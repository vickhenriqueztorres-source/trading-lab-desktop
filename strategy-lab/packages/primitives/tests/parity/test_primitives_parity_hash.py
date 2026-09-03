"""R-PRIM-6: public series produces a stable canonical hash for every primitive output."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from primitives import REGISTRY, Candle, Output

PARITY_DIR = Path(__file__).parent


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
    raw_rows = json.loads((PARITY_DIR / "series_10k.json").read_text(encoding="utf-8"))
    indicators = {name: indicator_type() for name, indicator_type in REGISTRY.items()}
    digest = hashlib.sha256()
    for raw in raw_rows:
        candle = Candle.model_validate(
            {
                "ts": raw["ts"],
                "o": Decimal(raw["o"]),
                "h": Decimal(raw["h"]),
                "l": Decimal(raw["l"]),
                "c": Decimal(raw["c"]),
                "tick_vol": raw["tick_vol"],
            }
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


def test_primitives_parity_hash() -> None:
    expected = (PARITY_DIR / "EXPECTED_SHA256").read_text(encoding="ascii").strip()
    assert compute_parity_hash() == expected


def test_primitives_parity_is_repeatable_in_one_process() -> None:
    assert compute_parity_hash() == compute_parity_hash()
