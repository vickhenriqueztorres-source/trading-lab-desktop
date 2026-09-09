"""CAT-02: bot executes the public recipe/evidence/capability contract independently."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from apps.core.families import agreed_direction
from apps.core.families.primitives.base import Output
from apps.core.manifest_client import (
    DEFAULT_PARITY_SHA256,
    FAMILY_COMPONENTS,
    ManifestClient,
    ManifestConsumerCapabilities,
    Rejected,
    canonical_bytes,
    evaluate_manifest_bytes,
)

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "strategy-lab"
    / "contracts"
    / "strategy_contract_vectors.v2.json"
)
VECTORS = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _hash_without_own_hash(value):
    payload = {key: item for key, item in value.items() if key != "sha256"}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _capabilities(raw):
    return ManifestConsumerCapabilities(
        execution_semantics=frozenset(raw["execution_semantics"]),
        products=frozenset(raw["products"]),
        timeframes=frozenset(raw["timeframes"]),
        max_warmup_candles=raw["max_warmup_candles"],
        tick_volume=raw["tick_volume"],
    )


def test_contract_and_case_hashes_are_stable():
    assert _hash_without_own_hash(VECTORS) == VECTORS["sha256"]
    for case in VECTORS["manifest_cases"]:
        assert _hash_without_own_hash(case) == case["sha256"]


@pytest.mark.parametrize("case", VECTORS["manifest_cases"], ids=lambda item: item["id"])
def test_manifest_v12_acceptance_and_capability_cases(case):
    document, reason = evaluate_manifest_bytes(
        json.dumps(case["document"], ensure_ascii=False).encode("utf-8"),
        {"A": bytes.fromhex(VECTORS["public_keys"]["A"])},
        allow_test_keys=True,
        expected_primitives_version="1.0.0",
        expected_parity_sha256=DEFAULT_PARITY_SHA256,
        capabilities=_capabilities(case["capabilities"]),
    )
    assert reason == case["reason_code"]
    assert (document is not None) is case["accepted"]


def test_current_runtime_refuses_v2_until_incremental_engine_exists():
    case = next(item for item in VECTORS["manifest_cases"] if item["id"] == "v12_real_observation")
    client = ManifestClient(
        public_keys={"A": bytes.fromhex(VECTORS["public_keys"]["A"])},
        allow_test_keys=True,
    )
    result = client.accept(json.dumps(case["document"], ensure_ascii=False).encode("utf-8"))
    assert isinstance(result, Rejected)
    assert result.reason_code == "MANIFEST_EXECUTION_SEMANTICS_UNSUPPORTED"


def test_family_composition_is_allowlisted_and_volume_is_derived():
    for case in VECTORS["composition_cases"]:
        components = FAMILY_COMPONENTS[case["family"]]
        assert list(components) == case["components"]
        assert ("tick_volume_ratio" in components) is case["requires_tick_volume"]


def test_consensus_vectors_match_bot_composition():
    for case in VECTORS["consensus_cases"]:
        outputs = [
            Output(direction=case[name], value=Decimal("1"), meta={})
            for name in ("regime", "trigger", "confirm")
        ]
        direction, reason = agreed_direction(*outputs, regime_allowed=case["regime_allowed"])
        assert ("none" if direction is None else direction.value.lower()) == case["direction"]
        assert reason == case["reason"]


def test_bootstrap_and_settlement_vectors_are_unambiguous():
    for case in VECTORS["bootstrap_cases"]:
        if not case["identity_matches"]:
            reason = "BOOTSTRAP_IDENTITY_MISMATCH"
        elif not case["continuous"]:
            reason = "BOOTSTRAP_GAP"
        elif case["have"] < case["need"]:
            reason = "WARMING_UP"
        else:
            reason = "READY"
        assert reason == case["reason"]
    for case in VECTORS["settlement_cases"]:
        before = Decimal(case["close_t"])
        after = Decimal(case["close_t1"])
        won = after > before if case["direction"] == "call" else after < before
        assert won is case["won"]


def test_telemetry_is_opt_in_and_excludes_private_identity():
    telemetry = VECTORS["telemetry"]
    assert telemetry["opt_in_required"] is True
    assert not set(telemetry["allowed_fields"]) & set(telemetry["forbidden_fields"])
