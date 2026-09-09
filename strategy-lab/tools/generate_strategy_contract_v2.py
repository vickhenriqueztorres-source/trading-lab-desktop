"""Generate the public CAT-02 strategy semantics vectors with test keys only."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from manifest_schema.canonical import canonical_bytes
from manifest_schema.families import FAMILY_COMPONENTS, Family, family_warmup_required
from manifest_schema.recipe import recipe_fingerprint
from manifest_schema.signing import sign

ROOT = Path(__file__).resolve().parents[1]
PARITY = (ROOT / "packages/primitives/tests/parity/EXPECTED_SHA256").read_text().strip()

FAMILY_PARAMS: dict[Family, dict[str, str]] = {
    "F1": {
        "adx_len": "14",
        "adx_max": "20",
        "bb_len": "20",
        "bb_k": "2.0",
        "rsi_len": "7",
        "rsi_lo": "20",
        "rsi_hi": "80",
    },
    "F2": {
        "ema_short": "5",
        "ema_medium": "10",
        "ema_long": "20",
        "pullback_len": "20",
        "pullback_tolerance": "0.002",
        "body_max": "0.35",
        "wick_min": "0.5",
    },
    "F3": {
        "level_support": "1.10",
        "level_resistance": "1.12",
        "level_tolerance": "0.0001",
        "body_max": "0.35",
        "wick_min": "0.5",
    },
    "F4": {
        "bb_len": "20",
        "bb_k": "2",
        "width_median_len": "20",
        "width_ratio_max": "0.5",
        "break_len": "20",
        "volume_len": "20",
        "volume_min": "1.5",
    },
    "F5": {"quadrant_window": "3", "rsi_len": "7", "rsi_lo": "20", "rsi_hi": "80"},
}


def _entry(family: Family, *, status: str = "observation") -> dict[str, Any]:
    params = FAMILY_PARAMS[family]
    components = FAMILY_COMPONENTS[family]
    entry: dict[str, Any] = {
        "key": f"{family.lower()}:EURUSD:M1:00-06:r1",
        "family": family,
        "display_name_pt": f"Receita pública {family}",
        "asset": "EURUSD",
        "timeframe": "M1",
        "hours_utc": [0, 6],
        "params": params,
        "validated": {
            "p_hat": "0.578",
            "wilson_lower": "0.561",
            "p_min_at_validation": "0.541",
            "payout_min": "0.84",
            "n": 1240,
            "ops_per_day": "11.2",
            "worst_streak": 6,
            "result_1000_ops_stake10": "182.00",
            "windows_passed": "8/8",
            "holdout_passed": True,
        },
        "status": status,
        "management": {"stake_pct": "1.0", "martingale_steps_max": 2, "paroli": True},
        "warmup_required": family_warmup_required(family, params, [0, 6]),
        "recipe_revision": 1,
        "composition": {
            "regime": components[0],
            "trigger": components[1],
            "confirm": components[2],
        },
        "capabilities": {
            "product": "binary_option",
            "tick_volume": "tick_volume_ratio" in components,
        },
    }
    entry["recipe_fingerprint"] = recipe_fingerprint(entry)
    return entry


def _manifest(family: Family = "F1", *, dataset_kind: str = "real_market") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "schema_revision": "1.2",
        "manifest_version": 21,
        "key_id": "A",
        "published_at": 1788350400,
        "expires_at": 1790942400,
        "primitives_version": "1.0.0",
        "primitives_parity_sha256": "sha256:" + PARITY,
        "research_run_id": "run_cat02_public",
        "execution_semantics_version": "tl.candle-close.v2",
        "dataset_evidence": {
            "dataset_id": "dataset_cat02_public",
            "fingerprint": "sha256:" + "1" * 64,
            "kind": dataset_kind,
            "from_ts": 1772323200,
            "to_ts": 1788307200,
            "coverage_pct": "99.50",
        },
        "telemetry": {
            "outcomes_supported": True,
            "opt_in_required": True,
            "schema_version": 1,
        },
        "strategies": [_entry(family)],
        "signature": "",
    }


def _with_hash(value: dict[str, Any]) -> dict[str, Any]:
    value["sha256"] = hashlib.sha256(canonical_bytes(value)).hexdigest()
    return value


def main() -> None:
    seed = bytes.fromhex((ROOT / "tests/keys/ed25519-test.seed.hex").read_text().strip())
    public = (ROOT / "tests/keys/ed25519-test.public.hex").read_text().strip()

    def signed(document: dict[str, Any]) -> dict[str, Any]:
        return sign(document, seed, "A", allow_test_keys=True).model_dump(
            mode="json", exclude_unset=True
        )

    real = signed(_manifest())
    synthetic = signed(_manifest(dataset_kind="synthetic"))
    f4 = signed(_manifest("F4"))

    bad_synthetic = deepcopy(synthetic)
    bad_synthetic["strategies"][0]["status"] = "approved"
    bad_composition = deepcopy(real)
    bad_composition["strategies"][0]["composition"]["trigger"] = "range_break"
    bad_fingerprint = deepcopy(real)
    bad_fingerprint["strategies"][0]["recipe_fingerprint"] = "sha256:" + "0" * 64
    bad_revision = deepcopy(real)
    bad_revision["schema_revision"] = "1.1"

    full_capability = {
        "execution_semantics": ["tl.candle-close.v2"],
        "products": ["binary_option"],
        "timeframes": ["M1", "M5", "M15"],
        "max_warmup_candles": 10000,
        "tick_volume": True,
    }
    cases = [
        {
            "id": "v12_real_observation",
            "document": real,
            "accepted": True,
            "reason_code": "MANIFEST_ACCEPTED",
            "schema_valid": True,
            "capabilities": full_capability,
        },
        {
            "id": "v12_synthetic_observation",
            "document": synthetic,
            "accepted": True,
            "reason_code": "MANIFEST_ACCEPTED",
            "schema_valid": True,
            "capabilities": full_capability,
        },
        {
            "id": "v12_synthetic_approved",
            "document": bad_synthetic,
            "accepted": False,
            "reason_code": "MANIFEST_SYNTHETIC_APPROVAL",
            "schema_valid": False,
            "capabilities": full_capability,
        },
        {
            "id": "v12_composition_mismatch",
            "document": bad_composition,
            "accepted": False,
            "reason_code": "MANIFEST_COMPOSITION_MISMATCH",
            "schema_valid": False,
            "capabilities": full_capability,
        },
        {
            "id": "v12_recipe_fingerprint_mismatch",
            "document": bad_fingerprint,
            "accepted": False,
            "reason_code": "MANIFEST_RECIPE_FINGERPRINT",
            "schema_valid": False,
            "capabilities": full_capability,
        },
        {
            "id": "v11_cannot_smuggle_v12_fields",
            "document": bad_revision,
            "accepted": False,
            "reason_code": "MANIFEST_RECIPE_CONTRACT_REVISION",
            "schema_valid": False,
            "capabilities": full_capability,
        },
        {
            "id": "v12_semantics_unsupported",
            "document": real,
            "accepted": False,
            "reason_code": "MANIFEST_EXECUTION_SEMANTICS_UNSUPPORTED",
            "schema_valid": True,
            "capabilities": {
                **full_capability,
                "execution_semantics": ["legacy.bot.window-replay.v1"],
            },
        },
        {
            "id": "v12_timeframe_unsupported",
            "document": real,
            "accepted": False,
            "reason_code": "MANIFEST_TIMEFRAME_UNSUPPORTED",
            "schema_valid": True,
            "capabilities": {**full_capability, "timeframes": ["M5"]},
        },
        {
            "id": "v12_warmup_capacity",
            "document": real,
            "accepted": False,
            "reason_code": "MANIFEST_WARMUP_CAPACITY_EXCEEDED",
            "schema_valid": True,
            "capabilities": {**full_capability, "max_warmup_candles": 10},
        },
        {
            "id": "v12_volume_unavailable",
            "document": f4,
            "accepted": False,
            "reason_code": "MANIFEST_TICK_VOLUME_UNAVAILABLE",
            "schema_valid": True,
            "capabilities": {**full_capability, "tick_volume": False},
        },
    ]
    for case in cases:
        _with_hash(case)

    composition_cases = [
        {
            "family": family,
            "components": list(FAMILY_COMPONENTS[family]),
            "requires_tick_volume": "tick_volume_ratio" in FAMILY_COMPONENTS[family],
        }
        for family in sorted(FAMILY_COMPONENTS)
    ]
    consensus_cases = [
        {
            "id": "all_call",
            "regime": "call",
            "trigger": "call",
            "confirm": "call",
            "regime_allowed": True,
            "direction": "call",
            "reason": "OK",
        },
        {
            "id": "all_put",
            "regime": "put",
            "trigger": "put",
            "confirm": "put",
            "regime_allowed": True,
            "direction": "put",
            "reason": "OK",
        },
        {
            "id": "regime_gate",
            "regime": "none",
            "trigger": "call",
            "confirm": "call",
            "regime_allowed": False,
            "direction": "none",
            "reason": "REGIME",
        },
        {
            "id": "no_signal",
            "regime": "none",
            "trigger": "none",
            "confirm": "none",
            "regime_allowed": True,
            "direction": "none",
            "reason": "NO_SIGNAL",
        },
        {
            "id": "disagree",
            "regime": "call",
            "trigger": "call",
            "confirm": "put",
            "regime_allowed": True,
            "direction": "none",
            "reason": "DISAGREE",
        },
    ]
    bootstrap_cases = [
        {
            "id": "ready_exact",
            "have": 28,
            "need": 28,
            "continuous": True,
            "identity_matches": True,
            "reason": "READY",
        },
        {
            "id": "warming",
            "have": 27,
            "need": 28,
            "continuous": True,
            "identity_matches": True,
            "reason": "WARMING_UP",
        },
        {
            "id": "gap",
            "have": 28,
            "need": 28,
            "continuous": False,
            "identity_matches": True,
            "reason": "BOOTSTRAP_GAP",
        },
        {
            "id": "identity",
            "have": 28,
            "need": 28,
            "continuous": True,
            "identity_matches": False,
            "reason": "BOOTSTRAP_IDENTITY_MISMATCH",
        },
    ]
    settlement_cases = [
        {"direction": "call", "close_t": "1.1000", "close_t1": "1.1001", "won": True},
        {"direction": "call", "close_t": "1.1000", "close_t1": "1.1000", "won": False},
        {"direction": "put", "close_t": "1.1000", "close_t1": "1.0999", "won": True},
        {"direction": "put", "close_t": "1.1000", "close_t1": "1.1000", "won": False},
    ]
    result: dict[str, Any] = {
        "contract_version": 2,
        "execution_semantics_version": "tl.candle-close.v2",
        "public_keys": {"A": public},
        "manifest_cases": cases,
        "composition_cases": composition_cases,
        "consensus_cases": consensus_cases,
        "bootstrap_cases": bootstrap_cases,
        "settlement_cases": settlement_cases,
        "telemetry": {
            "opt_in_required": True,
            "allowed_fields": [
                "client_id",
                "strategy_key",
                "recipe_revision",
                "manifest_version",
                "ts",
                "won",
                "payout_pct",
            ],
            "forbidden_fields": [
                "account_id",
                "email",
                "password",
                "token",
                "credential",
                "order_command",
            ],
        },
    }
    _with_hash(result)
    target = ROOT / "contracts/strategy_contract_vectors.v2.json"
    target.write_text(
        json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
