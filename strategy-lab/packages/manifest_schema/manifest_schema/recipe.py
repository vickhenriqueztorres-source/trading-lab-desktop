"""Versioned recipe identity without executable code or implicit defaults."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

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


def recipe_fingerprint(entry: Mapping[str, Any]) -> str:
    """Hash only fields that define numerical/execution behavior for one recipe revision."""

    payload = {name: entry[name] for name in RECIPE_IDENTITY_FIELDS}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
