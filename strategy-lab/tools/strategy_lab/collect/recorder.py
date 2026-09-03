"""Manual, bounded price-fixture recording; never strategy/bank/order writes (R-VEND-3)."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from strategy_lab.collect.iq_client import (
    LAB_ROOT,
    IQClient,
    IQClientError,
    IQClientProtocol,
    convert_candle,
    validate_asset,
)


def record_fixture(
    *,
    asset: str,
    from_ts: int,
    to_ts: int,
    output: Path,
    client_factory: Callable[[], IQClientProtocol] = IQClient,
    now_ts: int | None = None,
) -> dict[str, object]:
    """Record [from,to) M1, max 1000, exact coverage. Never overwrite an existing fixture.

    Only OHLC, UTC timestamps, tick volume and public provenance are serialized.
    Validation completes before opening a file. A failed recording leaves no fixture.
    """
    validate_asset(asset)
    now = int(datetime.now(UTC).timestamp()) if now_ts is None else now_ts
    if (
        type(from_ts) is not int
        or type(to_ts) is not int
        or from_ts < 0
        or from_ts % 60
        or to_ts % 60
        or to_ts <= from_ts
        or to_ts > now // 60 * 60 - 60
    ):
        raise IQClientError("IQ_FIXTURE_RANGE_INVALID")
    count = (to_ts - from_ts) // 60
    if count > 1000:
        raise IQClientError("IQ_FIXTURE_RANGE_TOO_LARGE")
    if output.exists():
        raise IQClientError("IQ_FIXTURE_EXISTS")
    client = client_factory()
    try:
        client.login()
        candles = client.fetch_candles(asset, 60, count, to_ts)
    finally:
        client.logout()
    if [c.ts for c in candles] != list(range(from_ts, to_ts, 60)):
        raise IQClientError("IQ_FIXTURE_COVERAGE_INCOMPLETE")
    rows = [
        {
            "from": candle.ts,
            "to": candle.ts + 60,
            "open": str(candle.o),
            "max": str(candle.h),
            "min": str(candle.l),
            "close": str(candle.c),
            "volume": candle.tick_vol,
        }
        for candle in candles
    ]
    # Revalidate protocol implementations/fakes too, not only IQClient.
    for row in rows:
        convert_candle(row, tf_s=60, end_ts=to_ts, now_ts=now)
    content: dict[str, object] = {
        "schema_version": 1,
        "provenance": "recorded",
        "asset": asset,
        "tf_s": 60,
        "from_ts": from_ts,
        "to_ts": to_ts,
        "collected_at": now,
        "count": count,
        "upstream_commit": (LAB_ROOT / "vendor/iqoptionapi/UPSTREAM_COMMIT").read_text().strip(),
        "candles": rows,
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    content["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    encoded = (json.dumps(content, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with output.open("xb") as handle:
            created = True
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    return {"asset": asset, "count": count, "sha256": content["sha256"]}
