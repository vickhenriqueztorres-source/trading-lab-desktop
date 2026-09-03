"""Hourly payout lookup; missing samples exclude trades (R-RES-4)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class PayoutPoint:
    asset: str
    hour_ts: int
    payout_return_ratio: Decimal | None
    samples: int


class PayoutLookup:
    def __init__(self, points: Iterable[PayoutPoint]) -> None:
        self._points = {(point.asset, point.hour_ts): point for point in points}

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, object]]) -> PayoutLookup:
        points: list[PayoutPoint] = []
        for row in rows:
            samples = _int_from_row(row, "samples", default=0)
            raw_value = row.get("payout_return_ratio", row.get("payout_pct"))
            payout = _decimal_or_none(raw_value)
            if payout is not None and payout > Decimal("1"):
                payout = payout / Decimal("100")
            points.append(
                PayoutPoint(
                    asset=str(row["asset"]),
                    hour_ts=_int_from_row(row, "hour_ts"),
                    payout_return_ratio=payout,
                    samples=samples,
                )
            )
        return cls(points)

    def payout(self, asset: str, ts: int) -> Decimal | None:
        hour_ts = ts - ts % 3600
        point = self._points.get((asset, hour_ts))
        if point is None or point.samples == 0:
            return None
        return point.payout_return_ratio


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _int_from_row(row: Mapping[str, object], key: str, default: int | None = None) -> int:
    value = row.get(key, default)
    if isinstance(value, bool) or value is None:
        raise ValueError("RES_ROW_INT_INVALID")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("RES_ROW_INT_INVALID")
