"""Tests for P10: publish pipeline, scorer, report, sprt, builder (R-PUB-1..5)."""

from __future__ import annotations

import ast
import io
import json
import os
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sprt.test import SprtDecision, WaldSprt
from strategy_lab.cli import main as cli_main
from strategy_lab.publish.builder import (
    PromotionError,
    build_manifest,
)
from strategy_lab.publish.differ import compute_diff, format_diff_report, prompt_confirmation
from strategy_lab.publish.preflight import (
    PreflightError,
    run_preflight,
    verify_contract_vectors_locally,
)
from strategy_lab.publish.signer import (
    InsecureKeyFileError,
    load_private_key_bytes,
    sign_manifest,
    verify_key_permissions,
)
from strategy_lab.publish.uploader import PublishUploadError, upload_manifest
from strategy_lab.research.report import run_synthetic_research
from strategy_lab.research.scorer import (
    calculate_payout_min,
    calculate_result_1000_ops_stake10,
    calculate_worst_streak,
    score_candidate,
)

LAB_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. Scorer tests (R-RES-9)
# ---------------------------------------------------------------------------


def test_scorer_metrics() -> None:
    """Test margin, score, worst streak, result and payout_min."""
    # Worst streak
    trades = [True, False, False, False, True, False, False, True]
    assert calculate_worst_streak(trades) == 3

    # Result 1000 ops stake 10
    # expected: 1000 * (0.60 * 0.85 - 0.40) * 10 = 1000 * (0.51 - 0.40) * 10 = 1100.00
    res = calculate_result_1000_ops_stake10(Decimal("0.60"), Decimal("0.85"))
    assert res == Decimal("1100.00")

    # Payout min
    # wilson_lower >= 1 / (1 + payout) + 0.015
    # for wilson_lower = 0.56:
    # at payout = 0.85: 1/1.85 + 0.015 = 0.54054 + 0.015 = 0.55554 <= 0.56 (passes!)
    # at payout = 0.84: 1/1.84 + 0.015 = 0.54348 + 0.015 = 0.55848 <= 0.56 (passes!)
    # at payout = 0.83: 1/1.83 + 0.015 = 0.54645 + 0.015 = 0.56145 > 0.56 (fails!)
    p_min_found = calculate_payout_min(Decimal("0.56"))
    assert p_min_found == Decimal("0.84")

    # Score calculation
    scored = score_candidate(
        p_hat=Decimal("0.58"),
        wilson_lower=Decimal("0.56"),
        p_min=Decimal("0.54"),
        n=1000,
        duration_days=Decimal("100"),
        won_series=trades,
        payout_med=Decimal("0.85"),
    )
    assert scored.margin == Decimal("0.02")
    assert scored.ops_per_day == Decimal("10.00")
    # score = 0.02 * sqrt(10) = 0.02 * 3.162277 = 0.0632
    assert scored.score == Decimal("0.0632")
    assert scored.worst_streak == 3
    assert scored.payout_min == Decimal("0.84")


# ---------------------------------------------------------------------------
# 2. Report and synthetic research tests (R-RES-11)
# ---------------------------------------------------------------------------


def test_synthetic_research_generates_ranking_and_candidates(tmp_path: Path) -> None:
    """strategy-lab research (dados sintéticos) -> ranking.md + candidates.json."""
    out_dir = tmp_path / "run_test"
    ranking_path, cand_path = run_synthetic_research("run_test", out_dir)

    assert ranking_path.exists()
    assert cand_path.exists()

    # Verify ranking markdown contains table headers and data
    md_content = ranking_path.read_text(encoding="utf-8")
    assert "# Research Ranking — Run run_test" in md_content
    assert "| Rank | Strategy Key | Score |" in md_content
    assert "EURUSD:F1:" in md_content
    assert "EURUSD:F2:" in md_content

    # Verify candidates JSON schema
    cand_data = json.loads(cand_path.read_text(encoding="utf-8"))
    assert cand_data["research_run_id"] == "run_test"
    assert len(cand_data["candidates"]) == 2
    for item in cand_data["candidates"]:
        assert "key" in item
        assert "family" in item
        assert "validated" in item
        assert "p_hat" in item["validated"]
        assert "wilson_lower" in item["validated"]
        assert "payout_min" in item["validated"]


# ---------------------------------------------------------------------------
# 3. SPRT Package tests (R-PUB-5, R-BOT-7)
# ---------------------------------------------------------------------------


def test_wald_sprt_hypothesis_and_decisions() -> None:
    """Test Wald's SPRT state machine, LLR updates, and decisions."""
    sprt = WaldSprt(p_0=Decimal("0.58"), p_1=Decimal("0.54"))

    # When all wins occur, LLR should drift negative toward ACCEPT_H0
    result_wins = sprt.evaluate_series([True] * 50)
    assert result_wins.decision == SprtDecision.ACCEPT_H0
    assert result_wins.llr <= result_wins.lower_bound

    # When all losses occur, LLR should drift positive toward REJECT_H0
    sprt_loss = WaldSprt(p_0=Decimal("0.58"), p_1=Decimal("0.54"))
    result_loss = sprt_loss.evaluate_series([False] * 50)
    assert result_loss.decision == SprtDecision.REJECT_H0
    assert result_loss.llr >= result_loss.upper_bound


def test_sprt_promotion_eligibility() -> None:
    """R-PUB-5: promote observation -> approved only if >= 200 ops or >= 30 days."""
    sprt = WaldSprt(p_0=Decimal("0.58"), p_1=Decimal("0.54"))

    # Case 1: insufficient samples (< 200 ops and < 30 days)
    assert not sprt.is_eligible_for_promotion([True] * 50, days=10)

    # Case 2: >= 200 ops with acceptable win rate (60% wins > p_0 58%)
    good_series_220 = [True, True, True, False, False] * 44
    assert sprt.is_eligible_for_promotion(good_series_220, days=15)

    # Case 3: >= 30 days with acceptable win rate
    good_series_60 = [True, True, True, False, False] * 12
    assert sprt.is_eligible_for_promotion(good_series_60, days=35)

    # Case 4: >= 200 ops but heavy losses (rejected by SPRT)
    bad_outcomes = [False] * 100 + [True] * 100
    assert not sprt.is_eligible_for_promotion(bad_outcomes, days=40)


# ---------------------------------------------------------------------------
# 4. Builder tests (R-PUB-1, R-PUB-5)
# ---------------------------------------------------------------------------


def test_builder_observation_and_promotion(tmp_path: Path) -> None:
    """R-PUB-1, R-PUB-5: new strategies start in observation; promotion requires SPRT."""
    _, cand_path = run_synthetic_research("run_build_test", tmp_path)
    cand_data = json.loads(cand_path.read_text(encoding="utf-8"))

    # Build initial manifest: all entries MUST be born in 'observation'
    manifest = build_manifest("run_build_test", cand_data, key_id="A")
    assert manifest.manifest_version == 1
    assert len(manifest.strategies) == 2
    for st in manifest.strategies:
        assert st.status == "observation"

    # Test inclusion and exclusion filters
    first_key = manifest.strategies[0].key
    second_key = manifest.strategies[1].key

    manifest_inc = build_manifest("run_build_test", cand_data, include_keys=[first_key], key_id="A")
    assert len(manifest_inc.strategies) == 1
    assert manifest_inc.strategies[0].key == first_key

    manifest_exc = build_manifest("run_build_test", cand_data, exclude_keys=[first_key], key_id="A")
    assert len(manifest_exc.strategies) == 1
    assert manifest_exc.strategies[0].key == second_key

    # Test promotion: fails if live outcomes insufficient
    with pytest.raises(PromotionError):
        build_manifest(
            "run_build_test",
            cand_data,
            promote_keys=[first_key],
            live_outcomes_by_key={first_key: [True] * 50},  # < 200 ops
            live_days_by_key={first_key: 10},  # < 30 days
        )

    # Test promotion: succeeds when >= 200 ops without rejection
    manifest_promoted = build_manifest(
        "run_build_test",
        cand_data,
        promote_keys=[first_key],
        live_outcomes_by_key={first_key: [True, True, True, False, False] * 44},  # 220 ops
        live_days_by_key={first_key: 25},
    )
    promoted_entry = next(s for s in manifest_promoted.strategies if s.key == first_key)
    assert promoted_entry.status == "approved"


# ---------------------------------------------------------------------------
# 5. Preflight tests and local contract vector parity (R-PUB-2, R-ISO-2..3)
# ---------------------------------------------------------------------------


def test_preflight_verifies_all_contract_vectors_locally() -> None:
    """Preflight executes all 60 cases from contracts/manifest_acceptance_vectors.json."""
    passed_count = verify_contract_vectors_locally()
    assert passed_count == 60


def test_preflight_validates_assembled_manifest(tmp_path: Path) -> None:
    """Preflight validates valid signed manifest and catches parity divergence."""
    _, cand_path = run_synthetic_research("run_preflight_test", tmp_path)
    cand_data = json.loads(cand_path.read_text(encoding="utf-8"))

    priv_key = Ed25519PrivateKey.generate()
    pub_key_bytes = priv_key.public_key().public_bytes_raw()
    public_keys = {"A": pub_key_bytes}

    manifest = build_manifest("run_preflight_test", cand_data, key_id="A")
    signed_manifest = sign_manifest(manifest, priv_key.private_bytes_raw(), key_id="A")

    # Should pass preflight
    res = run_preflight(signed_manifest, public_keys=public_keys, run_contract_vectors=False)
    assert res.passed is True
    assert res.strategies_count == 2

    # Parity divergence fails preflight
    bad_parity_manifest = build_manifest(
        "run_preflight_test",
        cand_data,
        key_id="A",
        primitives_parity_sha256="sha256:" + "0" * 64,
    )
    signed_bad_parity = sign_manifest(bad_parity_manifest, priv_key.private_bytes_raw(), key_id="A")
    with pytest.raises(PreflightError) as exc_info:
        run_preflight(signed_bad_parity, public_keys=public_keys, run_contract_vectors=False)
    assert exc_info.value.reason_code == "MANIFEST_PRIMITIVES_PARITY"


def test_preflight_strictly_prohibits_bot_imports() -> None:
    """AST/import scan verifies strategy-lab never imports trading-lab-desktop (R-ISO-2..3)."""
    lab_tools_dir = LAB_ROOT / "tools" / "strategy_lab"
    lab_pkg_dir = LAB_ROOT / "packages"

    forbidden_prefixes = ("apps.", "apps", "core", "manifest_client")
    violations: list[str] = []

    for base in (lab_tools_dir, lab_pkg_dir):
        for py_file in base.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_bytes(), filename=str(py_file))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in forbidden_prefixes:
                            if alias.name == forbidden or alias.name.startswith(f"{forbidden}."):
                                violations.append(f"{py_file}:{node.lineno} imports '{alias.name}'")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    for forbidden in forbidden_prefixes:
                        if node.module == forbidden or node.module.startswith(f"{forbidden}."):
                            violations.append(
                                f"{py_file}:{node.lineno} imports from '{node.module}'"
                            )

    assert len(violations) == 0, "Found bot import violations in strategy-lab:\n" + "\n".join(
        violations
    )


# ---------------------------------------------------------------------------
# 6. Differ and prompt confirmation tests (R-PUB-3)
# ---------------------------------------------------------------------------


def test_differ_and_confirmation_prompt() -> None:
    """R-PUB-3: diff report and confirmation prompt requiring exact strategy count."""
    diff = compute_diff(
        None,
        {"manifest_version": 1, "strategies": [{"key": "strat1"}, {"key": "strat2"}]},
    )
    assert diff.total_new == 2
    assert "strat1" in diff.added
    assert "strat2" in diff.added

    report_text = format_diff_report(diff)
    assert "MANIFEST DIFF: N/A -> v1" in report_text
    assert "+ strat1" in report_text

    # Prompt confirmation: exact match returns True
    assert prompt_confirmation(2, input_fn=lambda _: "2") is True
    # Non-match returns False
    assert prompt_confirmation(2, input_fn=lambda _: "wrong") is False
    assert prompt_confirmation(2, input_fn=lambda _: "yes") is False


# ---------------------------------------------------------------------------
# 7. Signer and 0600 permission check tests (R-PUB-4)
# ---------------------------------------------------------------------------


def test_signer_checks_permissions_and_signs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-PUB-4: load Ed25519 key, reject 0644 insecure permissions, sign canonical bytes."""
    priv_key = Ed25519PrivateKey.generate()
    key_bytes = priv_key.private_bytes_raw()

    key_file = tmp_path / "A.pem"
    key_file.write_bytes(key_bytes)

    # Test permission check logic in POSIX mode
    monkeypatch.setattr(os, "name", "posix")

    class FakeStat:
        def __init__(self, mode: int) -> None:
            self.st_mode = mode

    # 1. Insecure mode (0644)
    monkeypatch.setattr(Path, "stat", lambda self: FakeStat(0o644))
    with pytest.raises(InsecureKeyFileError):
        verify_key_permissions(key_file)

    # 2. Secure mode (0600)
    monkeypatch.setattr(Path, "stat", lambda self: FakeStat(0o600))
    verify_key_permissions(key_file)

    # Reset os.name to actual
    monkeypatch.undo()

    # Loading key bytes
    loaded_bytes = load_private_key_bytes("A", keys_dir=tmp_path, verify_perms=False)
    assert loaded_bytes == key_bytes


# ---------------------------------------------------------------------------
# 8. Uploader tests (201, 401, 409, 422 handling)
# ---------------------------------------------------------------------------


class MockHttpResponse(io.BytesIO):
    status: int = 200


class MockHttpHandler:
    def __init__(self, status: int, response_dict: dict[str, Any]) -> None:
        self.status = status
        self.response_bytes = json.dumps(response_dict).encode("utf-8")

    def __call__(self, req: urllib.request.Request) -> io.BytesIO:
        if self.status == 201:
            resp = MockHttpResponse(self.response_bytes)
            resp.status = 201
            return resp
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=self.status,
            msg="HTTP Error",
            hdrs=req.headers,  # type: ignore[arg-type]
            fp=io.BytesIO(self.response_bytes),
        )


def test_uploader_status_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 201 success, 401 unauthorized, 409 conflict, and 422 unprocessable entity."""
    manifest_bytes = b'{"schema_version": 1}'

    # 1. Status 201 (Created)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout: MockHttpHandler(201, {"sha256": "abc123sha"})(req),
    )
    res201 = upload_manifest(manifest_bytes, endpoint_url="http://hub/publish")
    assert res201.status_code == 201
    assert res201.sha256 == "abc123sha"

    # 2. Status 401 (Signature invalid)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout: MockHttpHandler(401, {"error": "MANIFEST_SIGNATURE_INVALID"})(req),
    )
    with pytest.raises(PublishUploadError) as exc_401:
        upload_manifest(manifest_bytes, endpoint_url="http://hub/publish")
    assert exc_401.value.status_code == 401
    assert exc_401.value.error_code == "MANIFEST_SIGNATURE_INVALID"

    # 3. Status 409 (Conflict / version not newer)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout: MockHttpHandler(409, {"error": "MANIFEST_VERSION_NOT_NEWER"})(req),
    )
    with pytest.raises(PublishUploadError) as exc_409:
        upload_manifest(manifest_bytes, endpoint_url="http://hub/publish")
    assert exc_409.value.status_code == 409
    assert exc_409.value.error_code == "MANIFEST_VERSION_NOT_NEWER"

    # 4. Status 422 (Schema invalid)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout: MockHttpHandler(422, {"error": "MANIFEST_PARAM_RANGE"})(req),
    )
    with pytest.raises(PublishUploadError) as exc_422:
        upload_manifest(manifest_bytes, endpoint_url="http://hub/publish")
    assert exc_422.value.status_code == 422
    assert exc_422.value.error_code == "MANIFEST_PARAM_RANGE"


# ---------------------------------------------------------------------------
# 9. CLI end-to-end publish flow and --yes prohibition (R-PUB-3)
# ---------------------------------------------------------------------------


def test_cli_publish_prohibits_yes_flag() -> None:
    """R-PUB-3: verify CLI strictly refuses --yes flag."""
    exit_code = cli_main(["publish", "--run-id", "test", "--key-id", "A", "--yes"])
    assert exit_code == 1


def test_cli_publish_dry_run_success(tmp_path: Path) -> None:
    """Verify CLI publish in dry-run mode completes successfully with test key."""
    # Generate synthetic candidates
    cand_dir = tmp_path / "runs" / "run_cli_test"
    _, cand_path = run_synthetic_research("run_cli_test", cand_dir)

    # Set up key A
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    key_file = keys_dir / "A.pem"
    key_file.write_bytes(Ed25519PrivateKey.generate().private_bytes_raw())
    os.chmod(key_file, 0o600)

    exit_code = cli_main(
        [
            "publish",
            "--run-id",
            "run_cli_test",
            "--key-id",
            "A",
            "--candidates-file",
            str(cand_path),
            "--keys-dir",
            str(keys_dir),
            "--dry-run",
            "--allow-test-keys",
        ]
    )
    assert exit_code == 0
