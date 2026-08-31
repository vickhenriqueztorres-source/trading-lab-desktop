"""Deterministic intent keys and State Store duplicate checks."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Protocol


class IdempotencyStore(Protocol):
    def idempotency_key_exists(self, dedupe_key: str) -> bool: ...


def generate_dedupe_key(
    account_id: str,
    strategy_id: str,
    asset: str,
    candle_open_time: datetime | date | int | str,
    direction: str,
    duration: int | str,
    strategy_signal_version: str,
) -> str:
    """Return a stable SHA-256 key for one logical signal."""
    fields = (
        account_id.strip(),
        strategy_id.strip(),
        asset.strip(),
        _canonical_time(candle_open_time),
        direction.strip().upper(),
        str(duration),
        strategy_signal_version.strip(),
    )
    if any(not field for field in fields):
        raise ValueError("dedupe key fields cannot be empty")
    canonical = "\x1f".join(fields).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def is_duplicate(store: IdempotencyStore, dedupe_key: str) -> bool:
    return store.idempotency_key_exists(dedupe_key)


def _canonical_time(value: datetime | date | int | str) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


__all__ = ["generate_dedupe_key", "is_duplicate"]
