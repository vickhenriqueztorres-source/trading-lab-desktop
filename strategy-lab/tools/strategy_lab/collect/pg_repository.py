"""Supabase/Postgres repository for collect (R-HUB-1, R-COL-6, R-COL-10)."""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from primitives import Candle

from strategy_lab.collect.clock import utc_now_ts
from strategy_lab.collect.repository import GapRecord, RepositoryError, source_for_asset


class PostgresRepository:
    def __init__(self, db_url: str | None = None, *, force_source: bool = False) -> None:
        self._db_url = db_url or os.environ.get("SUPABASE_DB_URL", "")
        if not self._db_url:
            raise RepositoryError("SUPABASE_DB_URL_REQUIRED")
        self._force_source = force_source

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        psycopg = importlib.import_module("psycopg")
        with psycopg.connect(self._db_url) as connection:
            yield connection

    def watermark(self, asset: str) -> int | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("select max(ts) from public.candles where asset = %s", (asset,))
            value = cursor.fetchone()[0]
        return None if value is None else int(value)

    def upsert_candles(self, candles: list[Candle], source: str) -> int:
        if not candles:
            return 0
        asset = _asset_from_source(source)
        collected_at = utc_now_ts()
        rows = [
            (
                asset,
                candle.ts,
                candle.o,
                candle.h,
                candle.l,
                candle.c,
                candle.tick_vol,
                source,
                collected_at,
            )
            for candle in candles
        ]
        where_clause = "true" if self._force_source else "public.candles.source = excluded.source"
        sql = f"""
            insert into public.candles(asset, ts, o, h, l, c, tick_vol, source, collected_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (asset, ts) do update
            set o = excluded.o,
                h = excluded.h,
                l = excluded.l,
                c = excluded.c,
                tick_vol = excluded.tick_vol,
                source = excluded.source,
                collected_at = excluded.collected_at
            where {where_clause}
            returning xmax = 0 as inserted
        """
        inserted = 0
        with self._connect() as connection, connection.cursor() as cursor:
            for row in rows:
                cursor.execute(sql, row)
                returned = cursor.fetchone()
                if returned is not None and bool(returned[0]):
                    inserted += 1
        return inserted

    def record_gaps(self, asset: str, gaps: list[GapRecord]) -> None:
        if not gaps:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                """
                    insert into public.gaps(
                        asset, from_ts, to_ts, detected_at, in_session, resolved
                    )
                    values (%s, %s, %s, %s, %s, %s)
                    on conflict (asset, from_ts) do update
                    set to_ts = excluded.to_ts,
                        detected_at = excluded.detected_at,
                        in_session = excluded.in_session,
                        resolved = excluded.resolved
                    """,
                [
                    (
                        asset,
                        gap.from_ts,
                        gap.to_ts,
                        gap.detected_at,
                        gap.in_session,
                        gap.resolved,
                    )
                    for gap in gaps
                ],
            )

    def upsert_payout(self, asset: str, hour_ts: int, value: Decimal) -> None:
        payout_pct = value * Decimal(100)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    insert into public.payouts(asset, hour_ts, payout_pct, samples)
                    values (%s, %s, %s, 1)
                    on conflict (asset, hour_ts) do update
                    set payout_pct = (
                            (public.payouts.payout_pct * public.payouts.samples)
                            + excluded.payout_pct
                        ) / (public.payouts.samples + 1),
                        samples = public.payouts.samples + 1
                    """,
                (asset, hour_ts, payout_pct),
            )

    def record_run(self, report: dict[str, object]) -> None:
        started_at = report.get("started_at")
        if type(started_at) is not int:
            raise RepositoryError("COL_RUN_REPORT_INVALID")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    insert into public.collect_runs(run_id, started_at, report, status)
                    values (%s, %s, %s::jsonb, %s)
                    on conflict (run_id) do nothing
                    """,
                (
                    str(report["run_id"]),
                    started_at,
                    json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                    str(report["status"]),
                ),
            )


def make_source(asset: str) -> str:
    from strategy_lab.collect.runner import read_upstream_commit

    return source_for_asset(asset, read_upstream_commit())


def _asset_from_source(source: str) -> str:
    asset, separator, _tail = source.partition("|")
    if not separator or not asset:
        raise RepositoryError("COL_SOURCE_INVALID")
    return asset
