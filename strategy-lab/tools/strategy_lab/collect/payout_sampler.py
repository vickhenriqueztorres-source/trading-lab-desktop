"""Hourly payout sampling (R-COL-8)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from strategy_lab.collect.iq_client import IQClientProtocol
from strategy_lab.collect.repository import Repository


def hour_floor(ts: int) -> int:
    return ts // 3600 * 3600


def sample_payout(
    *,
    client: IQClientProtocol,
    repository: Repository,
    asset: str,
    now_ts: int,
    dry_run: bool = False,
    observed_clock: Callable[[], int] | None = None,
) -> Decimal | None:
    payout = client.fetch_payout(asset)
    if payout is None:
        return None
    if not dry_run:
        observed_at = observed_clock() if observed_clock is not None else now_ts
        if type(observed_at) is not int or observed_at < now_ts:
            raise ValueError("COL_PAYOUT_CLOCK_INVALID")
        repository.upsert_payout(
            asset,
            hour_floor(observed_at),
            payout,
            observed_at=observed_at,
            source="iqoptionapi:catalog-turbo",
        )
    return payout
