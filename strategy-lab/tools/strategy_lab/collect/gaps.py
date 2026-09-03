"""Gap detection over the expected M1 grid (R-COL-7)."""

from __future__ import annotations

from collections.abc import Iterable

from strategy_lab.collect.repository import GapRecord
from strategy_lab.collect.sessions import in_session


def classify_gaps(
    asset: str, expected: range, present_ts: Iterable[int], detected_at: int
) -> list[GapRecord]:
    present = set(present_ts)
    missing = [ts for ts in expected if ts not in present]
    if not missing:
        return []
    result: list[GapRecord] = []
    start = missing[0]
    previous = missing[0]
    current_flag = in_session(asset, start)
    for ts in missing[1:]:
        flag = in_session(asset, ts)
        if ts == previous + 60 and flag == current_flag:
            previous = ts
            continue
        result.append(
            GapRecord(
                asset=asset,
                from_ts=start,
                to_ts=previous + 60,
                detected_at=detected_at,
                in_session=current_flag,
            )
        )
        start = ts
        previous = ts
        current_flag = flag
    result.append(
        GapRecord(
            asset=asset,
            from_ts=start,
            to_ts=previous + 60,
            detected_at=detected_at,
            in_session=current_flag,
        )
    )
    return result
