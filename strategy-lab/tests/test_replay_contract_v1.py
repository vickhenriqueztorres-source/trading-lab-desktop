"""CAT-04: replay vectors are stable public artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from strategy_lab.research.replay_contract import build_replay_contract

CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "contracts" / "replay_contract_vectors.v1.json"
)


def _hash_without_own_hash(value: dict[str, object]) -> str:
    payload = {key: item for key, item in value.items() if key != "sha256"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_replay_contract_vectors_are_current_and_stable() -> None:
    committed = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    generated = build_replay_contract()
    assert committed == generated
    assert _hash_without_own_hash(committed) == committed["sha256"]
    for case in committed["cases"]:
        assert _hash_without_own_hash(case) == case["sha256"]


def test_replay_contract_covers_required_edge_cases() -> None:
    committed = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    coverage = committed["coverage"]
    assert set(coverage["families"]) == {"F1", "F2", "F3", "F4", "F5"}
    assert {"M1", "M5", "M15"}.issubset(set(coverage["timeframes"]))
    assert len(coverage["assets"]) >= 5
    stages = {signal["stage"] for case in committed["cases"] for signal in case["expected_trace"]}
    assert "TICK_VOLUME_UNAVAILABLE" in stages
    assert "OUTSIDE_HOURS" in stages
    assert any(case["excluded_settlement_gap"] > 0 for case in committed["cases"])
    assert any(
        trade["won"] is False for case in committed["cases"] for trade in case["expected_trades"]
    )
