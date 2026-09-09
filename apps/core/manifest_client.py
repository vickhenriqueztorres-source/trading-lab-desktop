"""Manifest client, fail-closed validation, and atomic cache persistence (R-BOT-1..4)."""

from __future__ import annotations

import base64
import binascii
import contextlib
import email.utils
import hashlib
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from apps.core.manifest_keys import BUILD_PROFILE, PUBLIC_KEYS, TEST_KEY

DECIMAL_PATTERN = r"^-?[0-9]+(\.[0-9]+)?$"
MAX_DECIMAL_LENGTH = 24
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 32
MAX_LIFETIME = 45 * 86400
MAX_SAFE_INTEGER = 9007199254740991
MARGIN = Decimal("0.015")
PAYOUT_STEP = Decimal("0.01")
OFFLINE_EXPIRATION_GRACE_S = 86400  # 24 hours
DEFAULT_POLL_INTERVAL_S = 900.0  # 15 minutes
DEFAULT_MANIFEST_PRIMARY_URL = (
    "https://jciclczthkbpvvqnrnbf.supabase.co/functions/v1/manifest_current"
)
DEFAULT_MANIFEST_MIRROR_URL = (
    "https://jciclczthkbpvvqnrnbf.supabase.co/storage/v1/object/public/manifests/current.json"
)
# Four cycles/hour, with at most primary + mirror each, caps catalog traffic at 8 GET/hour.
MANIFEST_MAX_POLL_CYCLES_PER_HOUR = 4
MANIFEST_POLL_BUDGET_WINDOW_S = 3600.0
MANIFEST_POLL_PRESSURE_RATIO = Decimal("0.75")
MANIFEST_HTTP_TIMEOUT_S = 5.0
DEFAULT_PARITY_SHA256 = "sha256:f3d4285fc5aa7d7801a565cbee815d70034049c7a963ec137a8fa07da18eae10"

FAMILY_SPECS: dict[str, dict[str, tuple[Decimal, Decimal, Decimal, str]]] = {
    "F1": {
        "adx_len": (Decimal(2), Decimal(100), Decimal(1), "int"),
        "adx_max": (Decimal(0), Decimal(100), Decimal(1), "decimal"),
        "bb_k": (Decimal("0.5"), Decimal(5), Decimal("0.1"), "decimal"),
        "bb_len": (Decimal(2), Decimal(200), Decimal(1), "int"),
        "rsi_hi": (Decimal(51), Decimal(99), Decimal(1), "decimal"),
        "rsi_len": (Decimal(2), Decimal(100), Decimal(1), "int"),
        "rsi_lo": (Decimal(1), Decimal(49), Decimal(1), "decimal"),
    },
    "F2": {
        "body_max": (Decimal("0.05"), Decimal("0.8"), Decimal("0.05"), "decimal"),
        "ema_long": (Decimal(4), Decimal(300), Decimal(1), "int"),
        "ema_medium": (Decimal(3), Decimal(150), Decimal(1), "int"),
        "ema_short": (Decimal(2), Decimal(100), Decimal(1), "int"),
        "pullback_len": (Decimal(2), Decimal(200), Decimal(1), "int"),
        "pullback_tolerance": (Decimal(0), Decimal("0.05"), Decimal("0.001"), "decimal"),
        "wick_min": (Decimal("0.2"), Decimal("0.9"), Decimal("0.05"), "decimal"),
    },
    "F3": {
        "body_max": (Decimal("0.05"), Decimal("0.8"), Decimal("0.05"), "decimal"),
        "level_resistance": (Decimal("1E-8"), Decimal(1000000), Decimal("1E-8"), "decimal"),
        "level_support": (Decimal("1E-8"), Decimal(1000000), Decimal("1E-8"), "decimal"),
        "level_tolerance": (Decimal(0), Decimal(10000), Decimal("1E-8"), "decimal"),
        "wick_min": (Decimal("0.2"), Decimal("0.9"), Decimal("0.05"), "decimal"),
    },
    "F4": {
        "bb_k": (Decimal("0.5"), Decimal(5), Decimal("0.1"), "decimal"),
        "bb_len": (Decimal(2), Decimal(200), Decimal(1), "int"),
        "break_len": (Decimal(2), Decimal(200), Decimal(1), "int"),
        "volume_len": (Decimal(2), Decimal(200), Decimal(1), "int"),
        "volume_min": (Decimal("0.5"), Decimal(5), Decimal("0.1"), "decimal"),
        "width_median_len": (Decimal(2), Decimal(200), Decimal(1), "int"),
        "width_ratio_max": (Decimal("0.1"), Decimal(1), Decimal("0.1"), "decimal"),
    },
    "F5": {
        "quadrant_window": (Decimal(3), Decimal(21), Decimal(2), "int"),
        "rsi_hi": (Decimal(51), Decimal(99), Decimal(1), "decimal"),
        "rsi_len": (Decimal(2), Decimal(100), Decimal(1), "int"),
        "rsi_lo": (Decimal(1), Decimal(49), Decimal(1), "decimal"),
    },
}

FAMILY_RELATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "F1": (("rsi_lo", "rsi_hi"),),
    "F2": (("ema_short", "ema_medium"), ("ema_medium", "ema_long")),
    "F3": (("level_support", "level_resistance"),),
    "F4": (),
    "F5": (("rsi_lo", "rsi_hi"),),
}

FAMILY_COMPONENTS: dict[str, tuple[str, str, str]] = {
    "F1": ("adx", "bb_close_outside", "rsi_extreme"),
    "F2": ("ema_alignment", "ema_pullback", "candle_rejection"),
    "F3": ("session_window", "level_touch", "candle_rejection"),
    "F4": ("bb_width_ratio", "range_break", "tick_volume_ratio"),
    "F5": ("session_window", "quadrant_majority", "rsi_extreme"),
}

RECIPE_IDENTITY_FIELDS = (
    "family",
    "asset",
    "timeframe",
    "hours_utc",
    "params",
    "composition",
    "capabilities",
    "warmup_required",
)


@dataclass(frozen=True, slots=True)
class ManifestConsumerCapabilities:
    execution_semantics: frozenset[str]
    products: frozenset[str]
    timeframes: frozenset[str]
    max_warmup_candles: int
    tick_volume: bool


DEFAULT_CONSUMER_CAPABILITIES = ManifestConsumerCapabilities(
    execution_semantics=frozenset({"legacy.bot.window-replay.v1"}),
    products=frozenset({"binary_option"}),
    timeframes=frozenset({"M1", "M5", "M15"}),
    max_warmup_candles=10_000,
    tick_volume=True,
)


def recipe_fingerprint(entry: Mapping[str, Any]) -> str:
    payload = {name: entry[name] for name in RECIPE_IDENTITY_FIELDS}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def assess_manifest_capabilities(
    manifest: Mapping[str, Any],
    capabilities: ManifestConsumerCapabilities,
) -> tuple[bool, str]:
    semantics = manifest.get("execution_semantics_version")
    if semantics is None:
        return True, "MANIFEST_LEGACY_CONTRACT"
    if semantics not in capabilities.execution_semantics:
        return False, "MANIFEST_EXECUTION_SEMANTICS_UNSUPPORTED"
    for entry in manifest.get("strategies", ()):
        if entry.get("status") == "rejected":
            continue
        required = entry.get("capabilities")
        if not isinstance(required, dict):
            return False, "MANIFEST_CAPABILITIES_MISSING"
        if required.get("product") not in capabilities.products:
            return False, "MANIFEST_PRODUCT_UNSUPPORTED"
        if entry.get("timeframe") not in capabilities.timeframes:
            return False, "MANIFEST_TIMEFRAME_UNSUPPORTED"
        warmup = entry.get("warmup_required")
        if type(warmup) is not int:
            return False, "MANIFEST_WARMUP_REQUIRED"
        if warmup > capabilities.max_warmup_candles:
            return False, "MANIFEST_WARMUP_CAPACITY_EXCEEDED"
        if required.get("tick_volume") is True and not capabilities.tick_volume:
            return False, "MANIFEST_TICK_VOLUME_UNAVAILABLE"
    return True, "MANIFEST_CAPABILITIES_SATISFIED"


def _check(value: object, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ValueError("MANIFEST_TOO_DEEP")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError("MANIFEST_INTEGER_RANGE")
        return
    if type(value) is str:
        value.encode("utf-8")
        return
    if type(value) is list:
        for item in value:
            _check(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("MANIFEST_KEY_TYPE")
            _check(key, depth + 1)
            _check(item, depth + 1)
        return
    raise ValueError("MANIFEST_NON_JSON_TYPE")


def _no_float(value: str) -> None:
    raise ValueError("MANIFEST_FLOAT_FORBIDDEN")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("MANIFEST_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def load_document(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("MANIFEST_TOO_LARGE")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("MANIFEST_JSON_INVALID") from None
    try:
        result: object = json.loads(
            decoded,
            parse_float=_no_float,
            parse_constant=_no_float,
            object_pairs_hook=_unique_pairs,
        )
    except ValueError as error:
        message = str(error)
        if message.startswith("MANIFEST_"):
            raise
        raise ValueError("MANIFEST_JSON_INVALID") from error
    if type(result) is not dict:
        raise ValueError("MANIFEST_ROOT_TYPE")
    _check(result)
    return result


def canonical_bytes(doc: dict[str, Any]) -> bytes:
    if type(doc) is not dict:
        raise ValueError("MANIFEST_ROOT_TYPE")
    if "signature" in doc:
        raise ValueError("MANIFEST_SIGNATURE_MUST_BE_EXCLUDED")
    _check(doc)
    encoded = json.dumps(
        doc,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("MANIFEST_TOO_LARGE")
    return encoded


def decimal_value(value: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("MANIFEST_SCHEMA_INVALID")
    if len(value) > MAX_DECIMAL_LENGTH or re.fullmatch(DECIMAL_PATTERN, value) is None:
        raise ValueError("MANIFEST_SCHEMA_INVALID")
    return Decimal(value)


def validate_range(value: str, spec: tuple[Decimal, Decimal, Decimal, str]) -> None:
    number = decimal_value(value)
    minimum, maximum, step, kind = spec
    if not minimum <= number <= maximum:
        raise ValueError("MANIFEST_PARAM_RANGE")
    if kind == "int" and number != number.to_integral_value():
        raise ValueError("MANIFEST_PARAM_INTEGER")
    n, d = number.as_integer_ratio()
    lo, ld = minimum.as_integer_ratio()
    st, sd = step.as_integer_ratio()
    if ((n * ld - lo * d) * sd) % (d * ld * st):
        raise ValueError("MANIFEST_PARAM_STEP")


def validate_lifetime(published: int, expires: int) -> None:
    if not 0 < expires - published <= MAX_LIFETIME:
        raise ValueError("MANIFEST_EXPIRATION")


def validate_payout(wilson: str, payout: str) -> None:
    lower, minimum = decimal_value(wilson), decimal_value(payout)
    if not Decimal(0) < minimum <= Decimal(1):
        raise ValueError("MANIFEST_PAYOUT_MIN")
    if minimum % PAYOUT_STEP:
        raise ValueError("MANIFEST_PAYOUT_GRID")
    with localcontext() as ctx:
        ctx.prec = 28
        ctx.rounding = ROUND_HALF_EVEN
        if lower < Decimal(1) / (Decimal(1) + minimum) + MARGIN:
            raise ValueError("MANIFEST_PAYOUT_UNSAFE")
        previous = minimum - PAYOUT_STEP
        if previous > 0 and lower >= Decimal(1) / (Decimal(1) + previous) + MARGIN:
            raise ValueError("MANIFEST_PAYOUT_NOT_MINIMUM")


def validate_manifest_schema(data: dict[str, Any]) -> None:
    allowed_top_keys = {
        "schema_version",
        "schema_revision",
        "manifest_version",
        "key_id",
        "published_at",
        "expires_at",
        "primitives_version",
        "primitives_parity_sha256",
        "research_run_id",
        "execution_semantics_version",
        "dataset_evidence",
        "telemetry",
        "strategies",
        "signature",
    }
    if not data.keys() <= allowed_top_keys:
        raise ValueError("MANIFEST_SCHEMA_INVALID")
    if "schema_version" not in data:
        raise ValueError("MANIFEST_SCHEMA_INVALID")
    schema_ver = data["schema_version"]
    if type(schema_ver) is not int:
        raise ValueError("MANIFEST_SCHEMA_VERSION")
    if schema_ver != 1:
        raise ValueError("MANIFEST_SCHEMA_INVALID")
    schema_revision = data.get("schema_revision")
    if "schema_revision" in data and schema_revision not in {"1.1", "1.2"}:
        raise ValueError("MANIFEST_SCHEMA_REVISION_UNSUPPORTED")

    revision12_root_fields = {"execution_semantics_version", "dataset_evidence", "telemetry"}
    revision12_entry_fields = {
        "recipe_revision",
        "recipe_fingerprint",
        "composition",
        "capabilities",
    }
    raw_strategies = data.get("strategies")
    if schema_revision != "1.2" and (
        any(name in data for name in revision12_root_fields)
        or any(
            isinstance(entry, dict) and any(name in entry for name in revision12_entry_fields)
            for entry in (raw_strategies if isinstance(raw_strategies, list) else ())
        )
    ):
        raise ValueError("MANIFEST_RECIPE_CONTRACT_REVISION")

    if schema_revision == "1.2":
        for required_key in ("execution_semantics_version", "dataset_evidence", "telemetry"):
            if required_key not in data:
                raise ValueError("MANIFEST_SCHEMA_INVALID")
        if data["execution_semantics_version"] != "tl.candle-close.v2":
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        dataset = data["dataset_evidence"]
        if not isinstance(dataset, dict) or dataset.keys() != {
            "dataset_id",
            "fingerprint",
            "kind",
            "from_ts",
            "to_ts",
            "coverage_pct",
        }:
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        dataset_id = dataset["dataset_id"]
        if (
            not isinstance(dataset_id, str)
            or len(dataset_id) > 120
            or re.fullmatch(r"^[A-Za-z0-9_.:-]+$", dataset_id) is None
        ):
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        fingerprint = dataset["fingerprint"]
        if (
            not isinstance(fingerprint, str)
            or re.fullmatch(r"^sha256:[0-9a-f]{64}$", fingerprint) is None
        ):
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        if dataset["kind"] not in {"real_market", "synthetic"}:
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        from_ts, to_ts = dataset["from_ts"], dataset["to_ts"]
        if (
            type(from_ts) is not int
            or type(to_ts) is not int
            or not 0 <= from_ts < to_ts <= MAX_SAFE_INTEGER
        ):
            raise ValueError("MANIFEST_DATASET_RANGE")
        coverage = decimal_value(dataset["coverage_pct"])
        if not 0 <= coverage <= 100:
            raise ValueError("MANIFEST_COVERAGE_RANGE")
        telemetry = data["telemetry"]
        if not isinstance(telemetry, dict) or telemetry.keys() != {
            "outcomes_supported",
            "opt_in_required",
            "schema_version",
        }:
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        if (
            type(telemetry["outcomes_supported"]) is not bool
            or telemetry["opt_in_required"] is not True
            or telemetry["schema_version"] != 1
            or type(telemetry["schema_version"]) is not int
        ):
            raise ValueError("MANIFEST_SCHEMA_INVALID")

    for required_key in (
        "manifest_version",
        "key_id",
        "published_at",
        "expires_at",
        "primitives_version",
        "primitives_parity_sha256",
        "research_run_id",
        "strategies",
    ):
        if required_key not in data:
            raise ValueError("MANIFEST_SCHEMA_INVALID")

    mv = data["manifest_version"]
    if type(mv) is not int or mv < 1 or mv > MAX_SAFE_INTEGER:
        raise ValueError("MANIFEST_SCHEMA_INVALID")

    key_id = data["key_id"]
    if key_id not in ("A", "B"):
        raise ValueError("MANIFEST_SCHEMA_INVALID")

    pub = data["published_at"]
    exp = data["expires_at"]
    if type(pub) is not int or pub < 0 or pub > MAX_SAFE_INTEGER:
        raise ValueError("MANIFEST_SCHEMA_INVALID")
    if type(exp) is not int or exp < 0 or exp > MAX_SAFE_INTEGER:
        raise ValueError("MANIFEST_SCHEMA_INVALID")

    validate_lifetime(pub, exp)

    pv = data["primitives_version"]
    if (
        not isinstance(pv, str)
        or not re.fullmatch(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", pv)
        or len(pv) > 32
    ):
        raise ValueError("MANIFEST_SCHEMA_INVALID")

    pps = data["primitives_parity_sha256"]
    if not isinstance(pps, str) or not re.fullmatch(r"^sha256:[0-9a-f]{64}$", pps):
        raise ValueError("MANIFEST_SCHEMA_INVALID")

    rrid = data["research_run_id"]
    if not isinstance(rrid, str) or not re.fullmatch(r"^[A-Za-z0-9_.-]+$", rrid) or len(rrid) > 96:
        raise ValueError("MANIFEST_SCHEMA_INVALID")

    strategies = data["strategies"]
    if not isinstance(strategies, list) or len(strategies) > 5000:
        raise ValueError("MANIFEST_SCHEMA_INVALID")

    strat_keys: list[str] = []
    for s in strategies:
        if not isinstance(s, dict):
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        allowed_strat_keys = {
            "key",
            "family",
            "display_name_pt",
            "asset",
            "timeframe",
            "hours_utc",
            "params",
            "validated",
            "status",
            "management",
            "reason_pt",
            "warmup_required",
            "recipe_revision",
            "recipe_fingerprint",
            "composition",
            "capabilities",
        }
        if not s.keys() <= allowed_strat_keys:
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        warmup_required = s.get("warmup_required")
        if schema_revision in {"1.1", "1.2"} and warmup_required is None:
            raise ValueError("MANIFEST_WARMUP_REQUIRED")
        if warmup_required is not None and (
            type(warmup_required) is not int or not 1 <= warmup_required <= 10_000
        ):
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        for req_k in (
            "key",
            "family",
            "display_name_pt",
            "asset",
            "timeframe",
            "hours_utc",
            "params",
            "validated",
            "status",
            "management",
        ):
            if req_k not in s:
                raise ValueError("MANIFEST_SCHEMA_INVALID")

        key = s["key"]
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"^[A-Za-z0-9_:.-]+$", key)
            or len(key) > 160
        ):
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        strat_keys.append(key)

        family = s["family"]
        if family not in ("F1", "F2", "F3", "F4", "F5"):
            raise ValueError("MANIFEST_SCHEMA_INVALID")

        if schema_revision == "1.2":
            for required_key in (
                "recipe_revision",
                "recipe_fingerprint",
                "composition",
                "capabilities",
            ):
                if required_key not in s:
                    raise ValueError("MANIFEST_RECIPE_CONTRACT_REQUIRED")
            recipe_revision = s["recipe_revision"]
            if (
                type(recipe_revision) is not int
                or recipe_revision < 1
                or recipe_revision > MAX_SAFE_INTEGER
            ):
                raise ValueError("MANIFEST_SCHEMA_INVALID")
            composition = s["composition"]
            if not isinstance(composition, dict) or composition.keys() != {
                "regime",
                "trigger",
                "confirm",
            }:
                raise ValueError("MANIFEST_SCHEMA_INVALID")
            declared_components = (
                composition["regime"],
                composition["trigger"],
                composition["confirm"],
            )
            if declared_components != FAMILY_COMPONENTS[family]:
                raise ValueError("MANIFEST_COMPOSITION_MISMATCH")
            required_capabilities = s["capabilities"]
            if not isinstance(required_capabilities, dict) or required_capabilities.keys() != {
                "product",
                "tick_volume",
            }:
                raise ValueError("MANIFEST_SCHEMA_INVALID")
            if (
                required_capabilities["product"] != "binary_option"
                or type(required_capabilities["tick_volume"]) is not bool
            ):
                raise ValueError("MANIFEST_SCHEMA_INVALID")
            needs_volume = "tick_volume_ratio" in FAMILY_COMPONENTS[family]
            if required_capabilities["tick_volume"] != needs_volume:
                raise ValueError("MANIFEST_VOLUME_CAPABILITY_MISMATCH")
            fingerprint = s["recipe_fingerprint"]
            if (
                not isinstance(fingerprint, str)
                or re.fullmatch(r"^sha256:[0-9a-f]{64}$", fingerprint) is None
            ):
                raise ValueError("MANIFEST_SCHEMA_INVALID")
            if fingerprint != recipe_fingerprint(s):
                raise ValueError("MANIFEST_RECIPE_FINGERPRINT")

        name = s["display_name_pt"]
        if (
            not isinstance(name, str)
            or len(name) < 1
            or len(name) > 160
            or re.search(r"[\x00-\x1f]", name)
        ):
            raise ValueError("MANIFEST_SCHEMA_INVALID")

        asset = s["asset"]
        if (
            not isinstance(asset, str)
            or not re.fullmatch(r"^[A-Z0-9]+(?:-OTC)?$", asset)
            or len(asset) > 32
        ):
            raise ValueError("MANIFEST_SCHEMA_INVALID")

        tf = s["timeframe"]
        if tf not in ("M1", "M5", "M15"):
            raise ValueError("MANIFEST_SCHEMA_INVALID")

        hours = s["hours_utc"]
        if not isinstance(hours, list) or len(hours) != 2:
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        h0, h1 = hours
        if type(h0) is not int or type(h1) is not int or not (0 <= h0 <= 24 and 0 <= h1 <= 24):
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        if not h0 < h1:
            raise ValueError("MANIFEST_HOURS_RANGE")

        params = s["params"]
        if not isinstance(params, dict):
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        specs = FAMILY_SPECS[family]
        if params.keys() != specs.keys():
            raise ValueError("MANIFEST_PARAM_KEYS")
        for pname, pspec in specs.items():
            pval = params[pname]
            if not isinstance(pval, str):
                raise ValueError("MANIFEST_SCHEMA_INVALID")
            validate_range(pval, pspec)
        for lower, upper in FAMILY_RELATIONS[family]:
            if decimal_value(params[lower]) >= decimal_value(params[upper]):
                raise ValueError("MANIFEST_PARAM_RELATION")

        val = s["validated"]
        if not isinstance(val, dict):
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        val_keys = {
            "p_hat",
            "wilson_lower",
            "p_min_at_validation",
            "payout_min",
            "n",
            "ops_per_day",
            "worst_streak",
            "result_1000_ops_stake10",
            "windows_passed",
            "holdout_passed",
        }
        if val.keys() != val_keys:
            raise ValueError("MANIFEST_SCHEMA_INVALID")

        for metric in ("p_hat", "wilson_lower", "p_min_at_validation"):
            mval = decimal_value(val[metric])
            if not 0 <= mval <= 1:
                raise ValueError("MANIFEST_PROBABILITY_RANGE")
        if decimal_value(val["wilson_lower"]) > decimal_value(val["p_hat"]):
            raise ValueError("MANIFEST_WILSON_ABOVE_ESTIMATE")
        if decimal_value(val["ops_per_day"]) < 0:
            raise ValueError("MANIFEST_OPS_NEGATIVE")

        vn = val["n"]
        if type(vn) is not int or vn < 1 or vn > MAX_SAFE_INTEGER:
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        ws = val["worst_streak"]
        if type(ws) is not int or ws < 0 or ws > MAX_SAFE_INTEGER:
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        if ws > vn:
            raise ValueError("MANIFEST_STREAK_RANGE")

        wp = val["windows_passed"]
        if not isinstance(wp, str) or not re.fullmatch(r"^[0-9]{1,6}/[1-9][0-9]{0,5}$", wp):
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        passed, total = (int(p) for p in wp.split("/"))
        if passed > total:
            raise ValueError("MANIFEST_WINDOWS_RANGE")

        if type(val["holdout_passed"]) is not bool:
            raise ValueError("MANIFEST_SCHEMA_INVALID")

        validate_payout(val["wilson_lower"], val["payout_min"])

        status = s["status"]
        if status not in ("approved", "observation", "rejected"):
            raise ValueError("MANIFEST_SCHEMA_INVALID")

        reason_pt = s.get("reason_pt")
        if status == "rejected" and (reason_pt is None or not str(reason_pt).strip()):
            raise ValueError("MANIFEST_REASON_REQUIRED")
        if status == "approved" and not val["holdout_passed"]:
            raise ValueError("MANIFEST_HOLDOUT_REQUIRED")

        mgmt = s["management"]
        if not isinstance(mgmt, dict):
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        if mgmt.keys() != {"stake_pct", "martingale_steps_max", "paroli"}:
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        stake = decimal_value(mgmt["stake_pct"])
        if not 0 < stake <= 100:
            raise ValueError("MANIFEST_STAKE_RANGE")
        mg = mgmt["martingale_steps_max"]
        if type(mg) is not int or mg < 0 or mg > 10:
            raise ValueError("MANIFEST_SCHEMA_INVALID")
        if type(mgmt["paroli"]) is not bool:
            raise ValueError("MANIFEST_SCHEMA_INVALID")

    if len(strat_keys) != len(set(strat_keys)):
        raise ValueError("MANIFEST_DUPLICATE_KEY")
    if (
        schema_revision == "1.2"
        and data["dataset_evidence"]["kind"] == "synthetic"
        and any(strategy["status"] == "approved" for strategy in strategies)
    ):
        raise ValueError("MANIFEST_SYNTHETIC_APPROVAL")


def verify_signature(
    doc: dict[str, Any],
    public_keys: Mapping[str, bytes],
    *,
    allow_test_keys: bool = False,
) -> bool:
    sig = doc.get("signature")
    key_id = doc.get("key_id")
    if not isinstance(sig, str) or not sig.startswith("ed25519:"):
        return False
    if not isinstance(key_id, str) or key_id not in public_keys:
        return False
    raw_key = public_keys[key_id]
    if raw_key == TEST_KEY and not allow_test_keys:
        return False
    encoded = sig.removeprefix("ed25519:")
    try:
        signature_bytes = base64.b64decode(encoded, validate=True)
    except binascii.Error:
        return False
    if len(signature_bytes) != 64 or base64.b64encode(signature_bytes).decode("ascii") != encoded:
        return False
    unsigned = {k: v for k, v in doc.items() if k != "signature"}
    try:
        payload = canonical_bytes(unsigned)
        key = Ed25519PublicKey.from_public_bytes(raw_key)
        key.verify(signature_bytes, payload)
        return True
    except (InvalidSignature, ValueError):
        return False


def evaluate_manifest_bytes(
    raw: bytes,
    public_keys: Mapping[str, bytes],
    *,
    allow_test_keys: bool = False,
    expected_primitives_version: str | None = None,
    expected_parity_sha256: str | None = None,
    capabilities: ManifestConsumerCapabilities | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Pure, bounded evaluation of manifest bytes returning (data, reason_code)."""
    try:
        doc = load_document(raw)
        validate_manifest_schema(doc)
    except ValueError as error:
        msg = str(error)
        return None, msg if msg.startswith("MANIFEST_") else "MANIFEST_SCHEMA_INVALID"

    if not verify_signature(doc, public_keys, allow_test_keys=allow_test_keys):
        return None, "MANIFEST_SIGNATURE_INVALID"

    if (
        expected_primitives_version is not None
        and doc.get("primitives_version") != expected_primitives_version
    ):
        return None, "MANIFEST_PRIMITIVES_VERSION"

    if (
        expected_parity_sha256 is not None
        and doc.get("primitives_parity_sha256") != expected_parity_sha256
    ):
        return None, "MANIFEST_PRIMITIVES_PARITY"

    if capabilities is not None:
        compatible, reason = assess_manifest_capabilities(doc, capabilities)
        if not compatible:
            return None, reason

    return doc, "MANIFEST_ACCEPTED"


@dataclass(frozen=True)
class ManifestRecord:
    schema_version: int
    manifest_version: int
    key_id: str
    published_at: int
    expires_at: int
    primitives_version: str
    primitives_parity_sha256: str
    research_run_id: str
    schema_revision: str | None
    execution_semantics_version: str | None
    dataset_evidence: dict[str, Any] | None
    telemetry: dict[str, Any] | None
    strategies: tuple[dict[str, Any], ...]
    signature: str
    raw_bytes: bytes

    @classmethod
    def from_dict(cls, data: dict[str, Any], raw_bytes: bytes) -> ManifestRecord:
        return cls(
            schema_version=int(data["schema_version"]),
            manifest_version=int(data["manifest_version"]),
            key_id=str(data["key_id"]),
            published_at=int(data["published_at"]),
            expires_at=int(data["expires_at"]),
            primitives_version=str(data["primitives_version"]),
            primitives_parity_sha256=str(data["primitives_parity_sha256"]),
            research_run_id=str(data["research_run_id"]),
            schema_revision=(
                None if data.get("schema_revision") is None else str(data["schema_revision"])
            ),
            execution_semantics_version=(
                None
                if data.get("execution_semantics_version") is None
                else str(data["execution_semantics_version"])
            ),
            dataset_evidence=(
                None if data.get("dataset_evidence") is None else dict(data["dataset_evidence"])
            ),
            telemetry=None if data.get("telemetry") is None else dict(data["telemetry"]),
            strategies=tuple(data.get("strategies", ())),
            signature=str(data.get("signature", "")),
            raw_bytes=raw_bytes,
        )


@dataclass(frozen=True)
class Accepted:
    manifest: ManifestRecord


@dataclass(frozen=True)
class Rejected:
    reason_code: str


class ClockProtocol(Protocol):
    def monotonic(self) -> float: ...
    def now_utc_epoch(self) -> int: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def now_utc_epoch(self) -> int:
        return int(datetime.now(UTC).timestamp())


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


class HttpTransportProtocol(Protocol):
    def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse: ...


def _http_header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    return next((value for key, value in headers.items() if key.casefold() == expected), None)


def _manifest_http_reason(response: HttpResponse, source: str) -> str:
    """Extract only a bounded, stable server reason; never forward arbitrary bodies."""

    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        reason = payload.get("error")
        if isinstance(reason, str) and re.fullmatch(r"HUB_[A-Z0-9_]{1,80}", reason):
            return reason
        code = payload.get("code")
        if source == "mirror" and code == "NoSuchKey":
            return "MANIFEST_MIRROR_OBJECT_MISSING"
    return f"MANIFEST_{source.upper()}_HTTP_{response.status_code}"


def _validated_manifest_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("MANIFEST_URL_INVALID")
    return url


class UrlLibManifestTransport:
    """Bounded HTTPS transport used only by the background manifest poller."""

    def __init__(self, *, timeout_s: float = MANIFEST_HTTP_TIMEOUT_S) -> None:
        if timeout_s <= 0:
            raise ValueError("MANIFEST_HTTP_TIMEOUT_INVALID")
        self._timeout_s = timeout_s

    def get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        _validated_manifest_url(url)
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", **(headers or {})},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                body = self._read_bounded(response)
                return HttpResponse(
                    status_code=int(response.status),
                    headers={str(key): str(value) for key, value in response.headers.items()},
                    body=body,
                )
        except urllib.error.HTTPError as error:
            body = b"" if error.code == 304 else self._read_bounded(error)
            return HttpResponse(
                status_code=int(error.code),
                headers={str(key): str(value) for key, value in error.headers.items()},
                body=body,
            )

    @staticmethod
    def _read_bounded(response: Any) -> bytes:
        declared = _http_header(response.headers, "Content-Length")
        if declared is not None:
            try:
                if int(declared) > MAX_JSON_BYTES:
                    raise ValueError("MANIFEST_TOO_LARGE")
            except ValueError as error:
                if str(error) == "MANIFEST_TOO_LARGE":
                    raise
                raise ValueError("MANIFEST_CONTENT_LENGTH_INVALID") from error
        body = bytes(response.read(MAX_JSON_BYTES + 1))
        if len(body) > MAX_JSON_BYTES:
            raise ValueError("MANIFEST_TOO_LARGE")
        return body


class ManifestPollState(StrEnum):
    IDLE = "IDLE"
    NOT_DUE = "NOT_DUE"
    NOT_MODIFIED = "NOT_MODIFIED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"


ManifestPrepareCallback = Callable[[ManifestRecord], Callable[[], None]]


class ManifestClient:
    """Fail-closed manifest consumer, atomic disk cache, and verification engine."""

    def __init__(
        self,
        clock: ClockProtocol | None = None,
        http: HttpTransportProtocol | None = None,
        cache_dir: Path | str | None = None,
        public_keys: Mapping[str, bytes] | None = None,
        primitives_version: str = "1.0.0",
        primitives_parity_sha256: str = DEFAULT_PARITY_SHA256,
        primary_url: str = DEFAULT_MANIFEST_PRIMARY_URL,
        mirror_url: str = DEFAULT_MANIFEST_MIRROR_URL,
        allow_test_keys: bool = False,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        capabilities: ManifestConsumerCapabilities = DEFAULT_CONSUMER_CAPABILITIES,
    ) -> None:
        self._clock: ClockProtocol = clock if clock is not None else SystemClock()
        self._http: HttpTransportProtocol | None = http
        self._cache_dir: Path | None = Path(cache_dir) if cache_dir is not None else None
        self._public_keys: Mapping[str, bytes] = (
            public_keys if public_keys is not None else PUBLIC_KEYS
        )
        self._primitives_version = primitives_version
        self._primitives_parity_sha256 = primitives_parity_sha256
        self._primary_url = _validated_manifest_url(primary_url)
        self._mirror_url = _validated_manifest_url(mirror_url)
        self._allow_test_keys = allow_test_keys or (BUILD_PROFILE == "test")
        self._poll_interval_s = poll_interval_s
        self._on_event = on_event
        self._capabilities = capabilities

        self._lock = threading.Lock()
        self._accept_lock = threading.Lock()
        self._current: ManifestRecord | None = None
        self._last_etags: dict[str, str] = {}
        self._last_poll_monotonic: float = float("-inf")
        self._last_poll_state = ManifestPollState.IDLE
        self._on_change_callbacks: list[Callable[[ManifestRecord], None]] = []
        self._prepare_change_callbacks: list[ManifestPrepareCallback] = []

        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._load_cache()

    @property
    def public_keys(self) -> Mapping[str, bytes]:
        return self._public_keys

    def on_change(self, callback: Callable[[ManifestRecord], None]) -> None:
        with self._lock:
            self._on_change_callbacks.append(callback)

    def on_prepare(self, callback: ManifestPrepareCallback) -> None:
        """Register a compiler which returns an infallible, atomic commit callback."""
        with self._lock:
            self._prepare_change_callbacks.append(callback)

    @property
    def last_poll_state(self) -> ManifestPollState:
        with self._lock:
            return self._last_poll_state

    def current(self) -> ManifestRecord | None:
        """In-memory atomic retrieval of the active validated manifest. Never performs I/O."""
        with self._lock:
            return self._current

    def is_expired(self) -> bool:
        """Check if active manifest is expired under offline 24h grace period."""
        with self._lock:
            if self._current is None:
                return True
            now = self._clock.now_utc_epoch()
            if now > self._current.expires_at + OFFLINE_EXPIRATION_GRACE_S:
                self._emit("manifest_expired", {"manifest_version": self._current.manifest_version})
                return True
            return False

    def accept(
        self,
        raw: bytes | str,
        response_date_header: str | None = None,
    ) -> Accepted | Rejected:
        """Evaluate raw bytes, apply strict contract checks, and persist atomically."""
        raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw

        with self._accept_lock:
            doc, code = evaluate_manifest_bytes(
                raw_bytes,
                self._public_keys,
                allow_test_keys=self._allow_test_keys,
                expected_primitives_version=self._primitives_version,
                expected_parity_sha256=self._primitives_parity_sha256,
                capabilities=self._capabilities,
            )
            if doc is None or code != "MANIFEST_ACCEPTED":
                self._emit("manifest_rejected", {"reason": code})
                return Rejected(code)

            manifest_version = int(doc["manifest_version"])
            expires_at = int(doc["expires_at"])

            with self._lock:
                if self._current is not None and manifest_version <= self._current.manifest_version:
                    reason = "MANIFEST_REJECTED_REGRESSIVE_VERSION"
                    self._emit("manifest_rejected", {"reason": reason})
                    return Rejected(reason)
                preparers = list(self._prepare_change_callbacks)

            if response_date_header is not None:
                try:
                    server_dt = email.utils.parsedate_to_datetime(response_date_header)
                    if server_dt.utcoffset() is None:
                        raise ValueError
                    server_epoch = int(server_dt.timestamp())
                except (ValueError, TypeError):
                    reason = "MANIFEST_SERVER_DATE_INVALID"
                    self._emit("manifest_rejected", {"reason": reason})
                    return Rejected(reason)
                if server_epoch > expires_at:
                    reason = "MANIFEST_REJECTED_EXPIRED"
                    self._emit("manifest_rejected", {"reason": reason})
                    return Rejected(reason)
            else:
                # Local cache may be displayed for 24 h after expiry, while the
                # catalog's stricter eligibility gate blocks entries at expires_at.
                now = self._clock.now_utc_epoch()
                if now > expires_at + OFFLINE_EXPIRATION_GRACE_S:
                    reason = "MANIFEST_REJECTED_EXPIRED"
                    self._emit("manifest_rejected", {"reason": reason})
                    return Rejected(reason)

            record = ManifestRecord.from_dict(doc, raw_bytes)
            try:
                commits = [prepare(record) for prepare in preparers]
            except Exception:
                reason = "MANIFEST_PREPARATION_FAILED"
                self._emit("manifest_rejected", {"reason": reason})
                return Rejected(reason)

            with self._lock:
                # Acceptance is serialized, but recheck documents the monotonic
                # invariant at the actual publication point.
                if self._current is not None and manifest_version <= self._current.manifest_version:
                    reason = "MANIFEST_REJECTED_REGRESSIVE_VERSION"
                    self._emit("manifest_rejected", {"reason": reason})
                    return Rejected(reason)
                try:
                    for commit in commits:
                        commit()
                except Exception:
                    reason = "MANIFEST_ATOMIC_SWAP_FAILED"
                    self._emit("manifest_rejected", {"reason": reason})
                    return Rejected(reason)
                self._current = record
                if self._cache_dir is not None:
                    self._write_cache_atomic(raw_bytes)
                callbacks = list(self._on_change_callbacks)

        self._emit("manifest_applied", {"version": record.manifest_version})
        for cb in callbacks:
            with contextlib.suppress(Exception):
                cb(record)

        return Accepted(record)

    def poll(self, force: bool = False) -> Accepted | Rejected | None:
        """Poll remote manifests: primary -> mirror -> keep cache. Fail-closed."""
        if self._http is None:
            return None

        now_mono = self._clock.monotonic()
        with self._lock:
            if not force and (now_mono - self._last_poll_monotonic < self._poll_interval_s):
                self._last_poll_state = ManifestPollState.NOT_DUE
                return None
            self._last_poll_monotonic = now_mono
            etags = dict(self._last_etags)

        last_rejection: Rejected | None = None
        for source, url in (("primary", self._primary_url), ("mirror", self._mirror_url)):
            headers: dict[str, str] = {}
            etag = etags.get(url)
            if etag:
                headers["If-None-Match"] = etag
            try:
                resp = self._http.get(url, headers)
            except Exception:
                self._emit("manifest_source_unavailable", {"source": source})
                continue
            if resp.status_code == 304:
                with self._lock:
                    current = self._current
                if current is None:
                    self._emit(
                        "manifest_source_rejected",
                        {"source": source, "reason_code": "MANIFEST_304_WITHOUT_LAST_GOOD"},
                    )
                    continue
                with self._lock:
                    self._last_poll_state = ManifestPollState.NOT_MODIFIED
                return None
            if resp.status_code != 200:
                self._emit(
                    "manifest_source_unavailable",
                    {
                        "source": source,
                        "status_code": resp.status_code,
                        "reason_code": _manifest_http_reason(resp, source),
                    },
                )
                continue

            with self._lock:
                current = self._current
            if current is not None and resp.body == current.raw_bytes:
                with self._lock:
                    response_etag = _http_header(resp.headers, "ETag")
                    if response_etag:
                        self._last_etags[url] = response_etag
                    self._last_poll_state = ManifestPollState.NOT_MODIFIED
                return None

            result = self.accept(resp.body, _http_header(resp.headers, "Date"))
            if isinstance(result, Accepted):
                with self._lock:
                    response_etag = _http_header(resp.headers, "ETag")
                    if response_etag:
                        self._last_etags[url] = response_etag
                    self._last_poll_state = ManifestPollState.APPLIED
                return result
            last_rejection = result
            self._emit(
                "manifest_source_rejected",
                {"source": source, "reason_code": result.reason_code},
            )

        with self._lock:
            self._last_poll_state = (
                ManifestPollState.REJECTED
                if last_rejection is not None
                else ManifestPollState.UNAVAILABLE
            )
        if last_rejection is not None:
            return last_rejection
        # Both failed: the verified in-memory/cache generation is preserved.
        return None

    def _load_cache(self) -> None:
        if self._cache_dir is None:
            return
        cache_file = self._cache_dir / "manifest.json"
        if not cache_file.exists():
            return
        try:
            raw = cache_file.read_bytes()
            doc, code = evaluate_manifest_bytes(
                raw,
                self._public_keys,
                allow_test_keys=self._allow_test_keys,
                expected_primitives_version=self._primitives_version,
                expected_parity_sha256=self._primitives_parity_sha256,
                capabilities=self._capabilities,
            )
            if doc is not None and code == "MANIFEST_ACCEPTED":
                expires_at = int(doc["expires_at"])
                now = self._clock.now_utc_epoch()
                if now <= expires_at + OFFLINE_EXPIRATION_GRACE_S:
                    self._current = ManifestRecord.from_dict(doc, raw)
                    return
        except Exception:
            pass
        # Corrupted or invalid cache: discard and force empty
        with contextlib.suppress(OSError):
            cache_file.unlink(missing_ok=True)
        self._current = None

    def _write_cache_atomic(self, raw_bytes: bytes) -> None:
        if self._cache_dir is None:
            return
        tmp_file = self._cache_dir / "manifest.json.tmp"
        target_file = self._cache_dir / "manifest.json"
        with contextlib.suppress(OSError):
            with open(tmp_file, "wb") as handle:
                handle.write(raw_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_file, target_file)

    def _emit(self, event_name: str, payload: dict[str, Any]) -> None:
        if self._on_event is not None:
            with contextlib.suppress(Exception):
                self._on_event(event_name, payload)


class ManifestRefreshService:
    """Bounded background polling; never participates in a signal or order cycle."""

    def __init__(
        self,
        client: ManifestClient,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        min_backoff_s: float = 15.0,
        max_backoff_s: float = DEFAULT_POLL_INTERVAL_S,
        jitter_ratio: float = 0.10,
        max_cycles_per_window: int = MANIFEST_MAX_POLL_CYCLES_PER_HOUR,
        budget_window_s: float = MANIFEST_POLL_BUDGET_WINDOW_S,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[float, float], float] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        if (
            poll_interval_s <= 0
            or min_backoff_s <= 0
            or max_backoff_s < min_backoff_s
            or not 0 <= jitter_ratio <= 0.25
            or max_cycles_per_window <= 0
            or budget_window_s <= 0
        ):
            raise ValueError("MANIFEST_REFRESH_CONFIG_INVALID")
        self._client = client
        self._poll_interval_s = poll_interval_s
        self._min_backoff_s = min_backoff_s
        self._max_backoff_s = max_backoff_s
        self._jitter_ratio = jitter_ratio
        self._max_cycles_per_window = max_cycles_per_window
        self._budget_window_s = budget_window_s
        self._monotonic = monotonic
        random_source = random.SystemRandom()
        self._jitter = jitter if jitter is not None else random_source.uniform
        self._on_event = on_event
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cycles: deque[float] = deque()
        self._failures = 0
        self._pressure_reported = False

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="manifest-refresh",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout_s: float = MANIFEST_HTTP_TIMEOUT_S + 1.0) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout_s))
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def run_once(self) -> ManifestPollState:
        """Execute one bounded refresh cycle; exposed for deterministic tests."""
        now = self._monotonic()
        event: tuple[str, dict[str, Any]] | None = None
        allowed = True
        with self._lock:
            cutoff = now - self._budget_window_s
            while self._cycles and self._cycles[0] <= cutoff:
                self._cycles.popleft()
            used = len(self._cycles)
            if used >= self._max_cycles_per_window:
                allowed = False
                event = (
                    "manifest_poll_budget_exhausted",
                    {"used_cycles": used, "limit_cycles": self._max_cycles_per_window},
                )
            else:
                self._cycles.append(now)
                used += 1
                pressure = Decimal(used) / Decimal(self._max_cycles_per_window)
                report_pressure = pressure >= MANIFEST_POLL_PRESSURE_RATIO
                if report_pressure and not self._pressure_reported:
                    self._pressure_reported = True
                    event = (
                        "manifest_poll_budget_pressure",
                        {"used_cycles": used, "limit_cycles": self._max_cycles_per_window},
                    )
                elif not report_pressure:
                    self._pressure_reported = False

        if event is not None:
            self._emit(*event)
        if not allowed:
            return ManifestPollState.NOT_DUE

        self._client.poll(force=True)
        state = self._client.last_poll_state
        if state in {ManifestPollState.APPLIED, ManifestPollState.NOT_MODIFIED}:
            self._failures = 0
        else:
            self._failures += 1
        return state

    def _run(self) -> None:
        delay = 0.0
        while not self._stop.wait(delay):
            state = self.run_once()
            if state in {ManifestPollState.APPLIED, ManifestPollState.NOT_MODIFIED}:
                base_delay = self._poll_interval_s
            elif state is ManifestPollState.NOT_DUE:
                base_delay = min(self._poll_interval_s, self._budget_window_s)
            else:
                # The configured maximum already bounds the delay. Capping the
                # exponent also prevents an unbounded integer after a very long
                # offline period.
                exponent = min(20, max(0, self._failures - 1))
                exponential_delay = min(
                    self._max_backoff_s,
                    self._min_backoff_s * (2**exponent),
                )
                # A failed origin must not burn every hourly cycle during the
                # first minutes of an outage. Spread attempts across the same
                # sliding window that enforces the hard budget.
                budget_spacing = self._budget_window_s / self._max_cycles_per_window
                base_delay = max(exponential_delay, budget_spacing)
            radius = base_delay * self._jitter_ratio
            delay = max(0.0, base_delay + self._jitter(-radius, radius))

    def _emit(self, event_name: str, payload: dict[str, Any]) -> None:
        if self._on_event is not None:
            with contextlib.suppress(Exception):
                self._on_event(event_name, payload)
