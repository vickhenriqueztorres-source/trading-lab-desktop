"""R-MAN-1..7: explicit CLI to regenerate PUBLIC P02 contract artifacts, not production keys."""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from manifest_schema.canonical import canonical_bytes
from manifest_schema.export import export_schema
from manifest_schema.signing import sign

ROOT = Path(__file__).resolve().parents[1]
PARITY = (ROOT / "packages/primitives/tests/parity/EXPECTED_SHA256").read_text().strip()

FAMILY_EXAMPLES: dict[str, dict[str, str]] = {
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


def example() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "manifest_version": 14,
        "key_id": "A",
        "published_at": 1788350400,
        "expires_at": 1792238400,
        "primitives_version": "1.0.0",
        "primitives_parity_sha256": "sha256:" + PARITY,
        "research_run_id": "run_2026_09",
        "strategies": [
            {
                "key": "f1_reversal:EURUSD:M1:00-06",
                "family": "F1",
                "display_name_pt": "Reversão de Extremo",
                "asset": "EURUSD",
                "timeframe": "M1",
                "hours_utc": [0, 6],
                "params": FAMILY_EXAMPLES["F1"],
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
                "status": "observation",
                "management": {"stake_pct": "1.0", "martingale_steps_max": 2, "paroli": True},
            }
        ],
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    seed = bytes.fromhex((ROOT / "tests/keys/ed25519-test.seed.hex").read_text().strip())
    public = (ROOT / "tests/keys/ed25519-test.public.hex").read_text().strip()

    def signed(data, key_id="A"):
        return sign(data, seed, key_id, allow_test_keys=True).model_dump(
            mode="json", exclude_unset=True
        )

    base = signed(example())
    write_json(ROOT / "tests/fixtures/manifest_example.json", base)
    export_schema(ROOT / "schema/manifest.v1.schema.json")
    cases = []

    def add(case_id, document, reason="MANIFEST_ACCEPTED", schema_valid=True, **options):
        case = {
            "id": case_id,
            "document": document,
            "accepted": reason == "MANIFEST_ACCEPTED",
            "reason_code": reason,
            "schema_valid": schema_valid,
            **options,
        }
        cases.append(case)

    add("valid_f1_a", base)
    add("valid_f1_b", signed(example(), "B"))
    for family, params in FAMILY_EXAMPLES.items():
        data = example()
        data["strategies"][0] = deepcopy(data["strategies"][0])
        data["strategies"][0]["family"] = family
        data["strategies"][0]["params"] = params
        data["strategies"][0]["key"] = family.lower() + ":EURUSD:M1:00-06"
        add("valid_" + family.lower(), signed(data))
    for status in ("approved", "rejected"):
        data = deepcopy(base)
        data["strategies"][0]["status"] = status
        if status == "rejected":
            data["strategies"][0]["reason_pt"] = "Não passou na validação."
        add("valid_" + status, signed(data))

    def mutate(case_id, path, value, reason, schema_valid=False):
        data = deepcopy(base)
        target = data
        for part in path[:-1]:
            target = target[part]
        if value is DELETE:
            del target[path[-1]]
        else:
            target[path[-1]] = value
        add(case_id, data, reason, schema_valid)

    DELETE = object()
    E = ["strategies", 0]
    mutations = [
        ("expired_window", ["expires_at"], base["expires_at"] + 1, "MANIFEST_EXPIRATION"),
        ("negative_window", ["expires_at"], base["published_at"] - 1, "MANIFEST_EXPIRATION"),
        ("zero_window", ["expires_at"], base["published_at"], "MANIFEST_EXPIRATION"),
        ("unknown_schema", ["schema_version"], 2, "MANIFEST_SCHEMA_INVALID"),
        ("bool_schema", ["schema_version"], True, "MANIFEST_SCHEMA_VERSION"),
        ("unknown_key_id", ["key_id"], "C", "MANIFEST_SCHEMA_INVALID"),
        ("bad_version", ["primitives_version"], "1.0", "MANIFEST_SCHEMA_INVALID"),
        ("bad_hash", ["primitives_parity_sha256"], "sha256:bad", "MANIFEST_SCHEMA_INVALID"),
        ("negative_timestamp", ["published_at"], -1, "MANIFEST_SCHEMA_INVALID"),
        ("unknown_family", E + ["family"], "F6", "MANIFEST_SCHEMA_INVALID"),
        ("bad_status", E + ["status"], "ready", "MANIFEST_SCHEMA_INVALID"),
        ("no_payout", E + ["validated", "payout_min"], DELETE, "MANIFEST_SCHEMA_INVALID"),
        ("param_range", E + ["params", "bb_len"], "201", "MANIFEST_PARAM_RANGE"),
        ("param_step", E + ["params", "bb_k"], "2.05", "MANIFEST_PARAM_STEP"),
        ("param_integer", E + ["params", "bb_len"], "20.5", "MANIFEST_PARAM_INTEGER"),
        ("param_missing", E + ["params", "adx_max"], DELETE, "MANIFEST_PARAM_KEYS"),
        ("param_extra", E + ["params", "unknown"], "1", "MANIFEST_PARAM_KEYS"),
        ("param_numeric", E + ["params", "bb_len"], 20, "MANIFEST_SCHEMA_INVALID"),
        ("param_exponent", E + ["params", "bb_k"], "2e0", "MANIFEST_SCHEMA_INVALID"),
        ("nan_string", E + ["validated", "p_hat"], "NaN", "MANIFEST_SCHEMA_INVALID"),
        ("infinite_string", E + ["validated", "p_hat"], "Infinity", "MANIFEST_SCHEMA_INVALID"),
        ("probability_range", E + ["validated", "p_hat"], "1.1", "MANIFEST_PROBABILITY_RANGE"),
        ("wilson_above_p", E + ["validated", "p_hat"], "0.5", "MANIFEST_WILSON_ABOVE_ESTIMATE"),
        ("unsafe_payout", E + ["validated", "payout_min"], "0.83", "MANIFEST_PAYOUT_UNSAFE"),
        (
            "nonminimal_payout",
            E + ["validated", "payout_min"],
            "0.85",
            "MANIFEST_PAYOUT_NOT_MINIMUM",
        ),
        ("offgrid_payout", E + ["validated", "payout_min"], "0.845", "MANIFEST_PAYOUT_GRID"),
        ("zero_payout", E + ["validated", "payout_min"], "0", "MANIFEST_PAYOUT_MIN"),
        ("negative_ops", E + ["validated", "ops_per_day"], "-1", "MANIFEST_OPS_NEGATIVE"),
        ("streak_range", E + ["validated", "worst_streak"], 1241, "MANIFEST_STREAK_RANGE"),
        ("bad_windows", E + ["validated", "windows_passed"], "9/8", "MANIFEST_WINDOWS_RANGE"),
        ("zero_n", E + ["validated", "n"], 0, "MANIFEST_SCHEMA_INVALID"),
        ("zero_stake", E + ["management", "stake_pct"], "0", "MANIFEST_STAKE_RANGE"),
        ("unbounded_mg", E + ["management", "martingale_steps_max"], 11, "MANIFEST_SCHEMA_INVALID"),
        ("hour_range", E + ["hours_utc"], [6, 6], "MANIFEST_HOURS_RANGE"),
        ("unknown_field", ["unexpected"], True, "MANIFEST_SCHEMA_INVALID"),
        ("unsigned", ["signature"], "", "MANIFEST_SIGNATURE_INVALID", True),
    ]
    for args in mutations:
        mutate(*args)

    data = deepcopy(base)
    data["strategies"][0]["status"] = "rejected"
    add("missing_reason", data, "MANIFEST_REASON_REQUIRED", False)
    data = deepcopy(base)
    data["strategies"][0]["status"] = "approved"
    data["strategies"][0]["validated"]["holdout_passed"] = False
    add("approved_without_holdout", data, "MANIFEST_HOLDOUT_REQUIRED", False)
    data = deepcopy(base)
    data["strategies"] *= 2
    add("duplicate_strategy", data, "MANIFEST_DUPLICATE_KEY", False)

    signature = bytearray(base64.b64decode(base["signature"].split(":", 1)[1]))
    signature[0] ^= 1
    mutate(
        "signature_one_byte",
        ["signature"],
        "ed25519:" + base64.b64encode(signature).decode("ascii"),
        "MANIFEST_SIGNATURE_INVALID",
        True,
    )
    mutate(
        "altered_signed_label",
        E + ["display_name_pt"],
        "Alterado",
        "MANIFEST_SIGNATURE_INVALID",
        True,
    )
    add("key_not_trusted", base, "MANIFEST_SIGNATURE_INVALID", True, trusted_key_ids=["B"])
    add(
        "production_rejects_test_key",
        base,
        "MANIFEST_SIGNATURE_INVALID",
        True,
        allow_test_keys=False,
    )
    add(
        "version_incompatible",
        base,
        "MANIFEST_PRIMITIVES_VERSION",
        True,
        expected_primitives_version="2.0.0",
    )
    add(
        "parity_incompatible",
        base,
        "MANIFEST_PRIMITIVES_PARITY",
        True,
        expected_parity_sha256="sha256:" + "0" * 64,
    )

    raw = json.dumps(base, ensure_ascii=False)
    for name, raw_json, reason in [
        (
            "float_parameter",
            raw.replace('"bb_len": "20"', '"bb_len": 20.0'),
            "MANIFEST_FLOAT_FORBIDDEN",
        ),
        (
            "integral_float_epoch",
            raw.replace('"published_at": 1788350400', '"published_at": 1788350400.0'),
            "MANIFEST_FLOAT_FORBIDDEN",
        ),
        ("duplicate_json_key", raw[:-1] + ', "schema_version": 1}', "MANIFEST_DUPLICATE_JSON_KEY"),
        ("nonfinite_json", raw.replace('"bb_k": "2.0"', '"bb_k": NaN'), "MANIFEST_FLOAT_FORBIDDEN"),
        ("truncated_json", raw[:-5], "MANIFEST_JSON_INVALID"),
        ("root_array", "[]", "MANIFEST_ROOT_TYPE"),
    ]:
        cases.append(
            {
                "id": name,
                "raw_json": raw_json,
                "accepted": False,
                "reason_code": reason,
                "schema_valid": False,
            }
        )
    for case in cases:
        case["sha256"] = hashlib.sha256(canonical_bytes(case)).hexdigest()
    envelope = {
        "contract_version": 1,
        "public_keys": {"A": public, "B": public},
        "allow_test_keys": True,
        "cases": cases,
    }
    envelope["sha256"] = hashlib.sha256(canonical_bytes(envelope)).hexdigest()
    write_json(ROOT / "contracts/manifest_acceptance_vectors.json", envelope)


if __name__ == "__main__":
    main()
