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
        self._transaction_connection: Any = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit one validated collect run atomically; no nested/shared writers."""
        if self._transaction_connection is not None:
            raise RepositoryError("COL_TRANSACTION_ALREADY_ACTIVE")
        psycopg = importlib.import_module("psycopg")
        try:
            with psycopg.connect(self._db_url) as connection:
                self._transaction_connection = connection
                try:
                    yield
                finally:
                    self._transaction_connection = None
        except RepositoryError:
            raise
        except Exception:
            raise RepositoryError("COL_DATABASE_TRANSACTION_FAILED") from None

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        if self._transaction_connection is not None:
            try:
                yield self._transaction_connection
            except RepositoryError:
                raise
            except Exception:
                raise RepositoryError("COL_DATABASE_QUERY_FAILED") from None
            return
        psycopg = importlib.import_module("psycopg")
        try:
            with psycopg.connect(self._db_url) as connection:
                yield connection
        except RepositoryError:
            raise
        except Exception:
            raise RepositoryError("COL_DATABASE_QUERY_FAILED") from None

    def watermark(self, asset: str) -> int | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select greatest(
                    (select max(ts) from public.candles where asset = %s),
                    (select max_closed_ts from public.collection_watermarks where asset = %s)
                )
                """,
                (asset, asset),
            )
            value = cursor.fetchone()[0]
        return None if value is None else int(value)

    def upsert_candles(self, candles: list[Candle], source: str) -> int:
        if not candles:
            return 0
        asset = _asset_from_source(source)
        collected_at = utc_now_ts()
        where_clause = "true" if self._force_source else "public.candles.source = excluded.source"
        sql = f"""
            insert into public.candles(asset, ts, o, h, l, c, tick_vol, source, collected_at)
            select *
            from unnest(
                %s::text[], %s::bigint[], %s::numeric[], %s::numeric[],
                %s::numeric[], %s::numeric[], %s::bigint[], %s::text[], %s::bigint[]
            ) as batch(asset, ts, o, h, l, c, tick_vol, source, collected_at)
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
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    [asset] * len(candles),
                    [candle.ts for candle in candles],
                    [candle.o for candle in candles],
                    [candle.h for candle in candles],
                    [candle.l for candle in candles],
                    [candle.c for candle in candles],
                    [candle.tick_vol for candle in candles],
                    [source] * len(candles),
                    [collected_at] * len(candles),
                ),
            )
            inserted = sum(1 for returned in cursor.fetchall() if bool(returned[0]))
            cursor.execute(
                """
                insert into public.collection_watermarks(asset, max_closed_ts, updated_at)
                values (%s, %s, %s)
                on conflict (asset) do update
                set max_closed_ts = greatest(
                        public.collection_watermarks.max_closed_ts,
                        excluded.max_closed_ts
                    ),
                    updated_at = excluded.updated_at
                """,
                (asset, max(candle.ts for candle in candles), collected_at),
            )
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

    def upsert_payout(
        self,
        asset: str,
        hour_ts: int,
        value: Decimal,
        *,
        observed_at: int,
        source: str,
    ) -> None:
        if hour_ts != observed_at - observed_at % 3600:
            raise RepositoryError("COL_PAYOUT_OBSERVATION_INVALID")
        payout_pct = value * Decimal(100)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    insert into public.payout_observations(
                        asset, observed_at, payout_pct, source
                    ) values (%s, %s, %s, %s)
                    on conflict (asset, observed_at) do nothing
                    returning payout_pct, source
                    """,
                (asset, observed_at, payout_pct, source),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                cursor.execute(
                    """
                        select payout_pct, source
                        from public.payout_observations
                        where asset = %s and observed_at = %s
                    """,
                    (asset, observed_at),
                )
                existing = cursor.fetchone()
                if existing is None or Decimal(existing[0]) != payout_pct or existing[1] != source:
                    raise RepositoryError("COL_PAYOUT_OBSERVATION_CONFLICT")
            cursor.execute(
                """
                    insert into public.payouts(asset, hour_ts, payout_pct, samples)
                    select %s, %s, avg(payout_pct), count(*)::integer
                    from public.payout_observations
                    where asset = %s
                      and observed_at >= %s
                      and observed_at < %s + 3600
                    on conflict (asset, hour_ts) do update
                    set payout_pct = excluded.payout_pct,
                        samples = excluded.samples
                """,
                (asset, hour_ts, asset, hour_ts, hour_ts),
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
