"""CAT-02: public recipe/evidence/capability contract executed by the Lab."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from manifest_schema.acceptance import evaluate
from manifest_schema.canonical import canonical_bytes
from manifest_schema.capabilities import ConsumerCapabilities
from manifest_schema.export import manifest_schema
from manifest_schema.families import FAMILY_COMPONENTS
from manifest_schema.models import Manifest
from pydantic import ValidationError
from schema_oracle import contract_validator

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads(
    (ROOT / "contracts/strategy_contract_vectors.v2.json").read_text(encoding="utf-8")
)


def _hash_without_own_hash(value):
    payload = {key: item for key, item in value.items() if key != "sha256"}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _capabilities(raw):
    return ConsumerCapabilities(
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
    manifest, reason = evaluate(
        json.dumps(case["document"], ensure_ascii=False).encode("utf-8"),
        {"A": bytes.fromhex(VECTORS["public_keys"]["A"])},
        allow_test_keys=True,
        expected_primitives_version="1.0.0",
        expected_parity_sha256=(
            "sha256:f3d4285fc5aa7d7801a565cbee815d70034049c7a963ec137a8fa07da18eae10"
        ),
        capabilities=_capabilities(case["capabilities"]),
    )
    assert reason == case["reason_code"]
    assert (manifest is not None) is case["accepted"]
    try:
        Manifest.model_validate(case["document"])
        model_valid = True
    except ValidationError:
        model_valid = False
    schema_valid = contract_validator(manifest_schema()).is_valid(case["document"])
    assert model_valid is case["schema_valid"]
    assert schema_valid is case["schema_valid"]


def test_family_composition_is_allowlisted_and_volume_is_derived():
    for case in VECTORS["composition_cases"]:
        components = FAMILY_COMPONENTS[case["family"]]
        assert list(components) == case["components"]
        assert ("tick_volume_ratio" in components) is case["requires_tick_volume"]


def test_bootstrap_vectors_are_unambiguous():
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


def test_consensus_vectors_define_direction_and_refusal():
    for case in VECTORS["consensus_cases"]:
        if not case["regime_allowed"]:
            direction, reason = "none", "REGIME"
        elif case["trigger"] == case["confirm"] == "none":
            direction, reason = "none", "NO_SIGNAL"
        elif case["trigger"] not in {"call", "put"}:
            direction, reason = "none", "TRIGGER"
        elif case["confirm"] not in {"call", "put"}:
            direction, reason = "none", "CONFIRM"
        elif case["trigger"] != case["confirm"] or (
            case["regime"] in {"call", "put"} and case["regime"] != case["trigger"]
        ):
            direction, reason = "none", "DISAGREE"
        else:
            direction, reason = case["trigger"], "OK"
        assert direction == case["direction"]
        assert reason == case["reason"]


def test_settlement_vectors_define_tie_as_loss():
    for case in VECTORS["settlement_cases"]:
        before = Decimal(case["close_t"])
        after = Decimal(case["close_t1"])
        won = after > before if case["direction"] == "call" else after < before
        assert won is case["won"]


def test_telemetry_contract_is_opt_in_and_contains_no_private_identity():
    telemetry = VECTORS["telemetry"]
    assert telemetry["opt_in_required"] is True
    assert not set(telemetry["allowed_fields"]) & set(telemetry["forbidden_fields"])
