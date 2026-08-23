from __future__ import annotations

from datetime import UTC, datetime, timedelta

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def datetime_from_epoch_ms(value: int) -> datetime:
    if value < 0:
        raise ValueError("epoch milliseconds cannot be negative")
    return _EPOCH + timedelta(milliseconds=value)
