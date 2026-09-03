"""Manifest client, fail-closed validation, and atomic cache persistence (R-BOT-1..4)."""

from __future__ import annotations

import base64
import binascii
import contextlib
import email.utils
import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path
from typing import Any, Protocol

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
        "manifest_version",
        "key_id",
        "published_at",
        "expires_at",
        "primitives_version",
        "primitives_parity_sha256",
        "research_run_id",
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
        }
        if not s.keys() <= allowed_strat_keys:
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
        primary_url: str = "https://storage.dualtrade.com/manifests/latest.json",
        mirror_url: str = "https://r2.dualtrade.com/manifests/latest.json",
        allow_test_keys: bool = False,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._clock: ClockProtocol = clock if clock is not None else SystemClock()
        self._http: HttpTransportProtocol | None = http
        self._cache_dir: Path | None = Path(cache_dir) if cache_dir is not None else None
        self._public_keys: Mapping[str, bytes] = (
            public_keys if public_keys is not None else PUBLIC_KEYS
        )
        self._primitives_version = primitives_version
        self._primitives_parity_sha256 = primitives_parity_sha256
        self._primary_url = primary_url
        self._mirror_url = mirror_url
        self._allow_test_keys = allow_test_keys or (BUILD_PROFILE == "test")
        self._poll_interval_s = poll_interval_s
        self._on_event = on_event

        self._lock = threading.Lock()
        self._current: ManifestRecord | None = None
        self._last_etag: str | None = None
        self._last_poll_monotonic: float = float("-inf")
        self._on_change_callbacks: list[Callable[[ManifestRecord], None]] = []

        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._load_cache()

    @property
    def public_keys(self) -> Mapping[str, bytes]:
        return self._public_keys

    def on_change(self, callback: Callable[[ManifestRecord], None]) -> None:
        with self._lock:
            self._on_change_callbacks.append(callback)

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

        doc, code = evaluate_manifest_bytes(
            raw_bytes,
            self._public_keys,
            allow_test_keys=self._allow_test_keys,
            expected_primitives_version=self._primitives_version,
            expected_parity_sha256=self._primitives_parity_sha256,
        )
        if doc is None or code != "MANIFEST_ACCEPTED":
            self._emit("manifest_rejected", {"reason": code})
            return Rejected(code)

        manifest_version = int(doc["manifest_version"])
        expires_at = int(doc["expires_at"])

        with self._lock:
            # Monotonic version requirement: strictly newer than current
            if self._current is not None and manifest_version <= self._current.manifest_version:
                reason = "MANIFEST_REJECTED_REGRESSIVE_VERSION"
                self._emit("manifest_rejected", {"reason": reason})
                return Rejected(reason)

            # Expiration check
            if response_date_header is not None:
                try:
                    server_dt = email.utils.parsedate_to_datetime(response_date_header)
                    server_epoch = int(server_dt.timestamp())
                    if server_epoch > expires_at:
                        reason = "MANIFEST_REJECTED_EXPIRED"
                        self._emit("manifest_rejected", {"reason": reason})
                        return Rejected(reason)
                except (ValueError, TypeError):
                    pass
            else:
                # Offline / local verification: 24h grace window
                now = self._clock.now_utc_epoch()
                if now > expires_at + OFFLINE_EXPIRATION_GRACE_S:
                    reason = "MANIFEST_REJECTED_EXPIRED"
                    self._emit("manifest_rejected", {"reason": reason})
                    return Rejected(reason)

            record = ManifestRecord.from_dict(doc, raw_bytes)
            self._current = record

            # Atomic cache update on disk
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
                return None
            self._last_poll_monotonic = now_mono
            etag = self._last_etag

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag

        # Try primary URL
        try:
            resp = self._http.get(self._primary_url, headers)
            if resp.status_code == 304:
                return None
            if resp.status_code == 200:
                result = self.accept(resp.body, resp.headers.get("Date"))
                if isinstance(result, Accepted):
                    with self._lock:
                        self._last_etag = resp.headers.get("ETag")
                return result
        except Exception:
            pass

        # Primary failed; try mirror URL
        try:
            resp = self._http.get(self._mirror_url, headers)
            if resp.status_code == 304:
                return None
            if resp.status_code == 200:
                result = self.accept(resp.body, resp.headers.get("Date"))
                if isinstance(result, Accepted):
                    with self._lock:
                        self._last_etag = resp.headers.get("ETag")
                return result
        except Exception:
            pass

        # Both failed: cache is preserved
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
