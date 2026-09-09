"""Point-in-time payout lookup without hourly lookahead (R-COL-8, R-RES-4)."""

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
    observed_at: int | None = None
    as_of_eligible: bool = True
    source: str = "explicit"

    def effective_observed_at(self) -> int | None:
        """Direct points are observations at hour start; legacy aggregate rows are not."""
        if not self.as_of_eligible:
            return None
        return self.hour_ts if self.observed_at is None else self.observed_at


@dataclass(frozen=True)
class PayoutDecision:
    value: Decimal | None
    reason: str
    observed_at: int | None = None


class PayoutLookup:
    def __init__(self, points: Iterable[PayoutPoint]) -> None:
        by_hour: dict[tuple[str, int], list[PayoutPoint]] = {}
        for point in points:
            if point.hour_ts % 3600 != 0 or point.samples < 0:
                raise ValueError("RES_PAYOUT_POINT_INVALID")
            observed_at = point.effective_observed_at()
            if observed_at is not None and not point.hour_ts <= observed_at < point.hour_ts + 3600:
                raise ValueError("RES_PAYOUT_POINT_INVALID")
            by_hour.setdefault((point.asset, point.hour_ts), []).append(point)
        self._points = {
            key: tuple(sorted(values, key=_sort_observed_at)) for key, values in by_hour.items()
        }

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, object]]) -> PayoutLookup:
        points: list[PayoutPoint] = []
        for row in rows:
            samples = _int_from_row(row, "samples", default=0)
            raw_value = row.get("payout_return_ratio", row.get("payout_pct"))
            payout = _decimal_or_none(raw_value)
            if payout is not None and payout > Decimal("1"):
                payout = payout / Decimal("100")
            observed_raw = row.get("observed_at")
            observed_at = None if observed_raw is None else _int_from_row(row, "observed_at")
            points.append(
                PayoutPoint(
                    asset=str(row["asset"]),
                    hour_ts=_int_from_row(row, "hour_ts"),
                    payout_return_ratio=payout,
                    samples=samples,
                    observed_at=observed_at,
                    as_of_eligible=observed_at is not None,
                    source=str(row.get("source", "legacy-hour-average")),
                )
            )
        return cls(points)

    def decision(self, asset: str, ts: int) -> PayoutDecision:
        hour_ts = ts - ts % 3600
        points = self._points.get((asset, hour_ts))
        if not points:
            return PayoutDecision(None, "RES_PAYOUT_UNKNOWN")
        eligible: list[PayoutPoint] = []
        saw_legacy = False
        saw_zero_samples = False
        saw_future = False
        for point in points:
            observed_at = point.effective_observed_at()
            if observed_at is None:
                saw_legacy = True
                continue
            if point.samples == 0 or point.payout_return_ratio is None:
                saw_zero_samples = True
                continue
            if observed_at > ts:
                saw_future = True
                continue
            eligible.append(point)
        if eligible:
            selected = max(eligible, key=lambda item: item.effective_observed_at() or 0)
            return PayoutDecision(
                selected.payout_return_ratio,
                "RES_PAYOUT_AVAILABLE",
                selected.effective_observed_at(),
            )
        if saw_future:
            return PayoutDecision(None, "RES_PAYOUT_OBSERVED_AFTER_SIGNAL")
        if saw_zero_samples:
            return PayoutDecision(None, "RES_PAYOUT_SAMPLES_ZERO")
        if saw_legacy:
            return PayoutDecision(None, "RES_PAYOUT_LEGACY_NO_ASOF")
        return PayoutDecision(None, "RES_PAYOUT_UNKNOWN")

    def payout(self, asset: str, ts: int) -> Decimal | None:
        return self.decision(asset, ts).value


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _sort_observed_at(point: PayoutPoint) -> int:
    observed_at = point.effective_observed_at()
    return 2**63 - 1 if observed_at is None else observed_at


def _int_from_row(row: Mapping[str, object], key: str, default: int | None = None) -> int:
    value = row.get(key, default)
    if isinstance(value, bool) or value is None:
        raise ValueError("RES_ROW_INT_INVALID")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("RES_ROW_INT_INVALID")
