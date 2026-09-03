"""Contract compliance test: execute all cases in manifest_acceptance_vectors.json on the bot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.core.manifest_client import (
    DEFAULT_PARITY_SHA256,
    Accepted,
    ManifestClient,
    Rejected,
    evaluate_manifest_bytes,
)

# Contract file location: read-only public artifact, no code imported from strategy-lab
CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "strategy-lab"
    / "contracts"
    / "manifest_acceptance_vectors.json"
)


def _load_contract_vectors() -> dict[str, Any]:
    if not CONTRACT_PATH.exists():
        pytest.skip(f"Contract vectors file not found at {CONTRACT_PATH}")
    result: dict[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return result


VECTORS = _load_contract_vectors()


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda case: case["id"])
def test_manifest_acceptance_vector_cases(case: dict[str, Any]) -> None:
    """Validate all 60 acceptance cases in manifest_acceptance_vectors.json on ManifestClient."""
    public_keys = {
        name: bytes.fromhex(value)
        for name, value in VECTORS["public_keys"].items()
        if name in case.get("trusted_key_ids", ("A", "B"))
    }
    raw = case.get("raw_json", json.dumps(case.get("document"), ensure_ascii=False)).encode("utf-8")

    manifest, code = evaluate_manifest_bytes(
        raw,
        public_keys,
        allow_test_keys=case.get("allow_test_keys", VECTORS.get("allow_test_keys", False)),
        expected_primitives_version=case.get("expected_primitives_version"),
        expected_parity_sha256=case.get("expected_parity_sha256"),
    )

    assert code == case["reason_code"]
    assert (manifest is not None) is case["accepted"]

    # Also test via ManifestClient.accept()
    client = ManifestClient(
        public_keys=public_keys,
        primitives_version=case.get("expected_primitives_version", "1.0.0"),
        primitives_parity_sha256=case.get("expected_parity_sha256", DEFAULT_PARITY_SHA256),
        allow_test_keys=case.get("allow_test_keys", VECTORS.get("allow_test_keys", False)),
    )
    result = client.accept(raw)
    if case["accepted"]:
        assert isinstance(result, Accepted)
        curr = client.current()
        assert curr is not None
        assert manifest is not None
        assert curr.manifest_version == int(manifest["manifest_version"])
    else:
        assert isinstance(result, Rejected)
        assert result.reason_code == case["reason_code"]


def test_public_parity_vector_sha256() -> None:
    """Verify default primitives parity SHA-256 matches the public parity hash."""
    expected_hash = "sha256:f3d4285fc5aa7d7801a565cbee815d70034049c7a963ec137a8fa07da18eae10"
    assert expected_hash == DEFAULT_PARITY_SHA256
