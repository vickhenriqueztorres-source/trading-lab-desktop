"""Post-run series invariants (R-COL-9)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from primitives import Candle


@dataclass(frozen=True)
class InvariantIssue:
    code: str
    from_ts: int
    to_ts: int


def check_invariants(candles: list[Candle]) -> list[InvariantIssue]:
    issues: list[InvariantIssue] = []
    seen: set[int] = set()
    for left, right in zip(candles, candles[1:], strict=False):
        if left.ts in seen:
            issues.append(InvariantIssue("COL_DUPLICATE_TS", left.ts, left.ts + 60))
        seen.add(left.ts)
        if right.ts <= left.ts:
            issues.append(InvariantIssue("COL_NON_MONOTONIC_TS", left.ts, right.ts))
    if candles and candles[-1].ts in seen:
        issues.append(InvariantIssue("COL_DUPLICATE_TS", candles[-1].ts, candles[-1].ts + 60))
    return issues + _jump_issues(candles)


def _jump_issues(candles: list[Candle]) -> list[InvariantIssue]:
    if len(candles) < 16:
        return []
    ranges = [
        _true_range(previous, current)
        for previous, current in zip(candles, candles[1:], strict=False)
    ]
    issues: list[InvariantIssue] = []
    for index in range(14, len(candles) - 1):
        atr = sum(ranges[index - 14 : index], Decimal("0")) / Decimal(14)
        if atr == 0:
            continue
        jump = abs(candles[index].c - candles[index + 1].o)
        if jump > Decimal(8) * atr:
            issues.append(
                InvariantIssue(
                    "COL_SUSPECT_JUMP",
                    candles[index].ts,
                    candles[index + 1].ts + 60,
                )
            )
    return issues


def _true_range(previous: Candle, current: Candle) -> Decimal:
    return max(current.h - current.l, abs(current.h - previous.c), abs(current.l - previous.c))
