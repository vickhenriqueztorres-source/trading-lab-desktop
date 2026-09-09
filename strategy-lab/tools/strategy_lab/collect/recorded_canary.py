"""Validated public recorder artifact for a live canary (R-COL-2, I-7, I-8).

A checksum proves local integrity, not broker authenticity. The reference must be
captured through record-fixture/record-canary and reviewed independently. It never
becomes research evidence or replaces the immutable synthetic CI fixture.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal, Never

from primitives import Candle
from pydantic import BaseModel, ConfigDict, Field

from strategy_lab.collect.iq_client import LAB_ROOT, validate_asset

MAX_CANARY_BYTES = 32_768
DEFAULT_RECORDED_CANARY = LAB_ROOT / "state/collection-canary.json"


class RecordedCanaryError(RuntimeError):
    """Stable public code only; never propagate raw file/credential contents."""


class _PriceRow(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    from_ts: int = Field(alias="from", ge=0)
    to: int
    open: str
    max: str
    min: str
    close: str
    volume: int | None = Field(ge=0)


class _Recording(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    schema_version: Literal[1]
    provenance: Literal["recorded"]
    asset: str
    tf_s: Literal[60]
    from_ts: int = Field(ge=0)
    to_ts: int
    collected_at: int = Field(ge=0)
    count: Literal[5]
    upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    candles: list[_PriceRow] = Field(min_length=5, max_length=5)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RecordedCanary:
    asset: str
    candles: tuple[Candle, ...]
    sha256: str


def _reject_number(value: str) -> Never:
    raise ValueError("CANARY_JSON_NUMBER_INVALID")


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("CANARY_DUPLICATE_KEY")
        result[key] = value
    return result


def load_recorded_canary(path: Path, *, now_ts: int) -> RecordedCanary:
    """Validate complete price-only reference before any login, NTP or DB write."""
    try:
        if type(now_ts) is not int or now_ts < 0:
            raise ValueError("CANARY_CLOCK_INVALID")
        with path.open("rb") as handle:
            encoded = handle.read(MAX_CANARY_BYTES + 1)
        if len(encoded) > MAX_CANARY_BYTES:
            raise ValueError("CANARY_TOO_LARGE")
        raw = json.loads(
            encoded,
            parse_float=_reject_number,
            parse_constant=_reject_number,
            object_pairs_hook=_unique_keys,
        )
        if not isinstance(raw, dict) or any(
            type(raw.get(key)) is not int
            for key in ("schema_version", "tf_s", "from_ts", "to_ts", "collected_at", "count")
        ):
            raise ValueError("CANARY_INTEGER_REQUIRED")
        recording = _Recording.model_validate(raw)
        unsigned = {key: value for key, value in raw.items() if key != "sha256"}
        canonical = json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if hashlib.sha256(canonical).hexdigest() != recording.sha256:
            raise ValueError("CANARY_HASH_INVALID")
        current_commit = (LAB_ROOT / "vendor/iqoptionapi/UPSTREAM_COMMIT").read_text().strip()
        if recording.upstream_commit != current_commit:
            raise ValueError("CANARY_VENDOR_MISMATCH")
        validate_asset(recording.asset)
        if (
            recording.from_ts % 60
            or recording.to_ts != recording.from_ts + 300
            or recording.collected_at > now_ts
            or recording.to_ts > recording.collected_at // 60 * 60 - 60
        ):
            raise ValueError("CANARY_RANGE_INVALID")
        candles = []
        for index, row in enumerate(recording.candles):
            if row.from_ts != recording.from_ts + index * 60 or row.to != row.from_ts + 60:
                raise ValueError("CANARY_COVERAGE_INVALID")
            prices = tuple(Decimal(value) for value in (row.open, row.max, row.min, row.close))
            if any(not price.is_finite() or price <= 0 for price in prices):
                raise ValueError("CANARY_PRICE_INVALID")
            candles.append(
                Candle(
                    ts=row.from_ts,
                    o=prices[0],
                    h=prices[1],
                    l=prices[2],
                    c=prices[3],
                    tick_vol=row.volume,
                )
            )
        return RecordedCanary(recording.asset, tuple(candles), recording.sha256)
    except FileNotFoundError:
        raise RecordedCanaryError("COL_RECORDED_CANARY_REQUIRED") from None
    except Exception:
        raise RecordedCanaryError("COL_RECORDED_CANARY_INVALID") from None
