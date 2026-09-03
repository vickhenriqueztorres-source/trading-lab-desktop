"""P05 Supabase/Postgres repository tests (R-HUB-1, R-HUB-2, R-HUB-8)."""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from primitives import Candle
from strategy_lab.collect.pg_repository import PostgresRepository
from strategy_lab.collect.repository import source_for_asset


def closed_candle(ts: int) -> Candle:
    return Candle(
        ts=ts,
        o=Decimal("1.10000000"),
        h=Decimal("1.11000000"),
        l=Decimal("1.09000000"),
        c=Decimal("1.10500000"),
        tick_vol=10,
    )


@pytest.mark.staging
def test_staging_upsert_is_idempotent_real() -> None:
    """R-COL-6/R-HUB-8: repeated UPSERT writes no new candles on the second run."""
    repository = PostgresRepository(os.environ["SUPABASE_STAGING_DB_URL"])
    asset = "EURUSD-OTC"
    ts = 1700000040
    source = source_for_asset(asset, "test-p05")
    first = repository.upsert_candles([closed_candle(ts)], source)
    second = repository.upsert_candles([closed_candle(ts)], source)
    assert first in {0, 1}
    assert second == 0
    assert repository.watermark(asset) is not None


@pytest.mark.staging
def test_staging_check_rejects_invalid_candle_real() -> None:
    """R-HUB-1: database CHECK constraints reject invalid OHLC bounds."""
    import psycopg

    db_url = os.environ["SUPABASE_STAGING_DB_URL"]
    with (
        psycopg.connect(db_url) as connection,
        connection.cursor() as cursor,
        pytest.raises(psycopg.Error),
    ):
        cursor.execute(
            """
                insert into public.candles(
                    asset, ts, o, h, l, c, tick_vol, source, collected_at
                )
                values (
                    'EURUSD-OTC', 1700000100, 1.20, 1.10, 1.00, 1.15, 1, 'bad', 1700000200
                )
                """
        )


@pytest.mark.staging
def test_staging_anon_rls_insert_live_outcome_and_no_read() -> None:
    """R-HUB-2: anon can insert own outcome through JWT and cannot read private tables."""
    import psycopg

    anon_url = os.environ.get("SUPABASE_STAGING_ANON_DB_URL")
    if not anon_url:
        pytest.skip("SUPABASE_STAGING_ANON_DB_URL is required for anon RLS test")
    client_id = uuid.uuid4()
    jwt_claims = {"role": "anon", "client_id": str(client_id)}
    with psycopg.connect(anon_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (str(jwt_claims).replace("'", '"'),),
        )
        cursor.execute(
            """
                insert into public.live_outcomes(client_id, strategy_key, ts, won, payout_pct)
                values (%s, 'test', 1700000040, true, 87.00)
                """,
            (client_id,),
        )
        with pytest.raises(psycopg.Error):
            cursor.execute("select count(*) from public.candles")
