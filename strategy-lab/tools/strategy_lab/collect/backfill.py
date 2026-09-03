"""Idempotent candle backfill (R-COL-3..7)."""

from __future__ import annotations

from dataclasses import dataclass, field

from primitives import Candle

from strategy_lab.collect.gaps import classify_gaps
from strategy_lab.collect.iq_client import IQClientError, IQClientProtocol, validate_asset
from strategy_lab.collect.repository import Repository, source_for_asset

MAX_BATCH = 1000
TIMEFRAME_S = 60


@dataclass
class BackfillResult:
    asset: str
    fetched: int = 0
    written: int = 0
    gaps_in_session: int = 0
    gaps_out_session: int = 0
    next_watermark: int | None = None
    issues: list[str] = field(default_factory=list)


def closed_candle_exclusive(now_ts: int) -> int:
    return now_ts // 60 * 60 - 60


def backfill_asset(
    *,
    client: IQClientProtocol,
    repository: Repository,
    asset: str,
    now_ts: int,
    upstream_commit: str,
    initial_from_ts: int | None = None,
    dry_run: bool = False,
) -> BackfillResult:
    asset = validate_asset(asset)
    end_exclusive = closed_candle_exclusive(now_ts)
    watermark = repository.watermark(asset)
    if watermark is None:
        start_ts = (
            initial_from_ts
            if initial_from_ts is not None
            else max(0, end_exclusive - MAX_BATCH * 60)
        )
    else:
        start_ts = watermark + 60
    if start_ts % 60 or start_ts < 0:
        raise IQClientError("COL_BACKFILL_RANGE_INVALID")
    result = BackfillResult(asset=asset, next_watermark=watermark)
    cursor = start_ts
    source = source_for_asset(asset, upstream_commit)
    while cursor < end_exclusive:
        batch_end = min(cursor + MAX_BATCH * 60, end_exclusive)
        expected = range(cursor, batch_end, TIMEFRAME_S)
        candles = client.fetch_candles(asset, TIMEFRAME_S, len(expected), batch_end)
        validated = _validate_batch(candles, cursor, batch_end)
        gaps = classify_gaps(asset, expected, [candle.ts for candle in validated], now_ts)
        result.fetched += len(validated)
        result.gaps_in_session += sum(1 for gap in gaps if gap.in_session)
        result.gaps_out_session += sum(1 for gap in gaps if not gap.in_session)
        if not dry_run:
            result.written += repository.upsert_candles(validated, source)
            repository.record_gaps(asset, gaps)
        result.next_watermark = max(
            (candle.ts for candle in validated), default=result.next_watermark
        )
        cursor = batch_end
    return result


def _validate_batch(candles: object, start_ts: int, end_ts: int) -> list[Candle]:
    if not isinstance(candles, list) or len(candles) > MAX_BATCH:
        raise IQClientError("COL_CANDLE_BATCH_INVALID")
    result: list[Candle] = []
    seen: set[int] = set()
    for item in candles:
        if not isinstance(item, Candle):
            raise IQClientError("COL_CANDLE_BATCH_INVALID")
        if item.ts < start_ts or item.ts >= end_ts or item.ts in seen:
            raise IQClientError("COL_CANDLE_BATCH_INVALID")
        seen.add(item.ts)
        result.append(item)
    if any(left.ts >= right.ts for left, right in zip(result, result[1:], strict=False)):
        raise IQClientError("COL_CANDLE_BATCH_INVALID")
    return result
