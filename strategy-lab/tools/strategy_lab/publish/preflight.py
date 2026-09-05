"""Preflight manifest verification and compliance contract test runner (R-PUB-2, R-ISO-2..3)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manifest_schema.models import Manifest
from manifest_schema.signing import verify

DEFAULT_PARITY_SHA256 = "sha256:f3d4285fc5aa7d7801a565cbee815d70034049c7a963ec137a8fa07da18eae10"
CONTRACT_VECTORS_PATH = (
    Path(__file__).resolve().parents[3] / "contracts" / "manifest_acceptance_vectors.json"
)


class PreflightError(Exception):
    """Raised when preflight verification fails."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(f"Preflight failed [{reason_code}]: {message}")
        self.reason_code = reason_code


@dataclass(frozen=True)
class PreflightResult:
    passed: bool
    manifest_version: int
    strategies_count: int
    primitives_parity_sha256: str
    vector_tests_passed: int


def verify_contract_vectors_locally(
    vectors_file: Path | None = None,
) -> int:
    """Execute all test cases in contracts/manifest_acceptance_vectors.json using manifest_schema.

    Strictly isolated: does NOT import apps/core/manifest_client.py or any bot module.
    """
    path = vectors_file or CONTRACT_VECTORS_PATH
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de vetores contratuais não encontrado: {path}")

    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    public_keys = {name: bytes.fromhex(value) for name, value in data["public_keys"].items()}

    cases = data["cases"]
    passed_cases = 0

    from manifest_schema.acceptance import evaluate

    for case in cases:
        raw_bytes = case.get(
            "raw_json", json.dumps(case.get("document"), ensure_ascii=False)
        ).encode("utf-8")

        trusted_keys = {
            k: v for k, v in public_keys.items() if k in case.get("trusted_key_ids", ("A", "B"))
        }

        manifest, reason_code = evaluate(
            raw_bytes,
            trusted_keys,
            allow_test_keys=case.get("allow_test_keys", data.get("allow_test_keys", False)),
            expected_primitives_version=case.get("expected_primitives_version"),
            expected_parity_sha256=case.get("expected_parity_sha256"),
        )
        accepted = manifest is not None

        if reason_code != case["reason_code"] or accepted != case["accepted"]:
            case_id = case["id"]
            exp_code = case["reason_code"]
            exp_acc = case["accepted"]
            raise PreflightError(
                reason_code,
                f"Caso {case_id} divergente: esperado {exp_code} (accepted={exp_acc}), "
                f"obtido {reason_code} (accepted={accepted})",
            )
        passed_cases += 1

    return passed_cases


def run_preflight(
    manifest: Manifest | dict[str, Any],
    public_keys: Mapping[str, bytes],
    allow_test_keys: bool = False,
    run_contract_vectors: bool = True,
    expected_parity_sha256: str = DEFAULT_PARITY_SHA256,
) -> PreflightResult:
    """Execute preflight check on candidate manifest before diff and upload."""
    if isinstance(manifest, Manifest):
        manifest_model = manifest
    else:
        try:
            manifest_model = Manifest.model_validate(manifest)
        except Exception as e:
            raise PreflightError("MANIFEST_SCHEMA_INVALID", str(e)) from e

    # Check signature if signed
    if manifest_model.signature:
        # Keep Pydantic's fields-set information: legacy v1 manifests omit the
        # additive v1.1 defaults from their signed canonical document.
        sig_ok = verify(manifest_model, dict(public_keys), allow_test_keys=allow_test_keys)
        if not sig_ok:
            raise PreflightError(
                "MANIFEST_SIGNATURE_INVALID",
                "Assinatura Ed25519 inválida ou chave não confiável.",
            )

    # Check primitives parity SHA-256
    if manifest_model.primitives_parity_sha256 != expected_parity_sha256:
        raise PreflightError(
            "MANIFEST_PRIMITIVES_PARITY",
            f"Paridade divergente: {manifest_model.primitives_parity_sha256}",
        )

    # Check strategy count
    if len(manifest_model.strategies) == 0:
        raise PreflightError("MANIFEST_STRATEGIES_EMPTY", "Manifesto vazio.")

    # Contract compliance vectors
    vectors_passed = 0
    if run_contract_vectors:
        vectors_passed = verify_contract_vectors_locally()

    return PreflightResult(
        passed=True,
        manifest_version=manifest_model.manifest_version,
        strategies_count=len(manifest_model.strategies),
        primitives_parity_sha256=manifest_model.primitives_parity_sha256,
        vector_tests_passed=vectors_passed,
    )
