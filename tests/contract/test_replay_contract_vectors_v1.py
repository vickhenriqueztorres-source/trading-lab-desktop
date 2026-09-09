"""CAT-04: desktop bot validates Strategy Lab replay vectors without importing Lab."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from apps.core.families import FAMILY_CLASSES
from apps.core.families.base import EvalResult
from packages.domain.market import MarketCandle
from packages.domain.models import Broker
from packages.strategies.models import RuntimeContext

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "strategy-lab"
    / "contracts"
    / "replay_contract_vectors.v1.json"
)
VECTORS = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _hash_without_own_hash(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "sha256"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_replay_contract_and_case_hashes_are_stable() -> None:
    assert _hash_without_own_hash(VECTORS) == VECTORS["sha256"]
    for case in VECTORS["cases"]:
        assert _hash_without_own_hash(case) == case["sha256"]


@pytest.mark.parametrize(
    "case",
    [case for case in VECTORS["cases"] if case["bot_comparable"]],
    ids=lambda item: item["id"],
)
def test_bot_families_match_public_replay_vectors_by_prefix(case: dict[str, Any]) -> None:
    family = FAMILY_CLASSES[case["family"]](
        strategy_key=case["id"],
        params=case["params"],
        hours_utc=case["hours_utc"],
        asset=case["asset"],
        timeframe=case["tf"],
    )
    market_candles = [_market_candle(raw, case["tf_seconds"]) for raw in case["candles"]]
    context = RuntimeContext(
        strategy_id=case["id"],
        strategy_version="contract",
        broker=Broker.IQ_OPTION,
        account_id="contract-account",
        product="binary",
        symbol=case["asset"],
        timeframe_seconds=case["tf_seconds"],
        configuration_version="contract",
    )

    for index, expected in enumerate(case["expected_trace"]):
        result = family.evaluate_detailed(market_candles[: index + 1], context)
        _assert_result_matches(result, expected, compare_outputs=case["compare_outputs"])


def _assert_result_matches(
    result: EvalResult,
    expected: dict[str, Any],
    *,
    compare_outputs: bool,
) -> None:
    assert result.stage == expected["stage"]
    actual_direction = "none" if result.direction is None else result.direction.value.lower()
    assert actual_direction == expected["direction"]
    if not compare_outputs or result.stage in {
        "WARMING_UP",
        "OUTSIDE_HOURS",
        "TICK_VOLUME_UNAVAILABLE",
    }:
        return
    for attr in ("regime", "trigger", "confirm"):
        output = getattr(result, attr)
        assert (None if output is None else output.direction) == expected[f"{attr}_direction"]
        assert (
            _decimal_or_none(None if output is None else output.value) == expected[f"{attr}_value"]
        )


def _market_candle(raw: dict[str, Any], tf_seconds: int) -> MarketCandle:
    close_time = datetime.fromtimestamp(int(raw["ts"]), UTC)
    return MarketCandle(
        broker=Broker.IQ_OPTION,
        broker_symbol="CONTRACT",
        timeframe_seconds=tf_seconds,
        open_time=close_time - timedelta(seconds=tf_seconds),
        close_time=close_time,
        open=Decimal(raw["o"]),
        high=Decimal(raw["h"]),
        low=Decimal(raw["l"]),
        close=Decimal(raw["c"]),
        tick_volume=raw["tick_vol"],
        is_closed=True,
    )


def _decimal_or_none(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")
