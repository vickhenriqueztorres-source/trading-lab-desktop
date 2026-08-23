from __future__ import annotations

import json
from typing import Any


def canonical_bytes(payload: Any) -> bytes:
    """Single stable JSON representation used by deterministic platform hashes."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
