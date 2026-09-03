"""R-MAN-1..7: complete public acceptance corpus, including hostile raw JSON."""

import hashlib
import json
from pathlib import Path

import pytest
from manifest_schema.acceptance import evaluate
from manifest_schema.canonical import canonical_bytes, load_document
from manifest_schema.models import Manifest
from pydantic import ValidationError
from schema_oracle import contract_validator

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads(
    (ROOT / "contracts/manifest_acceptance_vectors.json").read_text(encoding="utf-8")
)
SCHEMA = json.loads((ROOT / "schema/manifest.v1.schema.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda case: case["id"])
def test_hostile_manifests_rejected(case):
    """R-MAN-1..7: every case must match its explicit expected reason, no input persisted."""
    public_keys = {
        name: bytes.fromhex(value)
        for name, value in VECTORS["public_keys"].items()
        if name in case.get("trusted_key_ids", ("A", "B"))
    }
    raw = case.get("raw_json", json.dumps(case.get("document"), ensure_ascii=False)).encode()
    manifest, code = evaluate(
        raw,
        public_keys,
        allow_test_keys=case.get("allow_test_keys", VECTORS["allow_test_keys"]),
        expected_primitives_version=case.get("expected_primitives_version"),
        expected_parity_sha256=case.get("expected_parity_sha256"),
    )
    assert code == case["reason_code"]
    assert (manifest is not None) is case["accepted"]
    try:
        document = load_document(raw)
    except ValueError:
        assert case["schema_valid"] is False
        return
    try:
        Manifest.model_validate(document)
        model_valid = True
    except ValidationError:
        model_valid = False
    schema_valid = contract_validator(SCHEMA).is_valid(document)
    assert schema_valid is case["schema_valid"]
    assert model_valid is schema_valid


def test_public_vectors_and_individual_hashes():
    """R-MAN-4: hashing excludes only the envelope's own hash, never nested signatures."""
    for item in [VECTORS, *VECTORS["cases"]]:
        payload = {key: value for key, value in item.items() if key != "sha256"}
        assert hashlib.sha256(canonical_bytes(payload)).hexdigest() == item["sha256"]


def test_explicit_semantic_profile_required(fixture_document):
    """R-MAN-5: an unconfigured schema validator is NOT the consumption contract."""
    from copy import deepcopy

    from jsonschema import Draft202012Validator

    bad = deepcopy(fixture_document)
    bad["expires_at"] += 1
    assert Draft202012Validator(SCHEMA).is_valid(bad)
    assert not contract_validator(SCHEMA).is_valid(bad)
    missing_policy = dict(SCHEMA)
    del missing_policy["x-tl-policy-v1"]
    with pytest.raises(ValueError, match="MANIFEST_POLICY_UNSUPPORTED"):
        contract_validator(missing_policy)
