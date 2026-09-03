"""Hourly payout sampling (R-COL-8)."""

from __future__ import annotations

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
) -> Decimal | None:
    payout = client.fetch_payout(asset)
    if payout is None:
        return None
    if not dry_run:
        repository.upsert_payout(asset, hour_floor(now_ts), payout)
    return payout
