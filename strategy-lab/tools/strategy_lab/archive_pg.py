"""Postgres side of the verified cold-archive protocol (CAT-17, R-HUB-7)."""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from strategy_lab.archive import (
    ArchiveJob,
    ArchivePayoutObservation,
    ArchiveProof,
    ArchiveRow,
    ArchiveStatus,
)
from strategy_lab.collect.repository import RepositoryError


class PostgresArchiveRepository:
    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url or os.environ.get("SUPABASE_DB_URL", "")
        if not self._db_url:
            raise RepositoryError("SUPABASE_DB_URL_REQUIRED")

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        psycopg = importlib.import_module("psycopg")
        rows_factory = importlib.import_module("psycopg.rows").dict_row
        with psycopg.connect(self._db_url, row_factory=rows_factory) as connection:
            yield connection

    def plan(self) -> str | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("select public.archive_old_candles() as job_id")
            row = cursor.fetchone()
        value = None if row is None else row["job_id"]
        return None if value is None else str(value)

    def claim(self, worker_id: str, lease_seconds: int) -> ArchiveJob | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select * from public.claim_cold_archive_job(%s, %s)",
                (worker_id, lease_seconds),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return ArchiveJob(
            job_id=str(row["job_id"]),
            asset=str(row["asset"]),
            from_ts=int(row["from_ts"]),
            to_ts=int(row["to_ts"]),
            expected_count=int(row["expected_count"]),
            claim_token=str(row["claim_token"]),
            status=ArchiveStatus(str(row["status"])),
        )

    def frozen_rows(self, job: ArchiveJob) -> list[ArchiveRow]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select asset, ts, o, h, l, c, tick_vol, source, collected_at,
                       payout_observations
                from public.cold_archive_job_rows
                where job_id = %s
                order by asset, ts
                """,
                (job.job_id,),
            )
            rows = list(cursor.fetchall())
        return [_archive_row(row) for row in rows]

    def mark_verified(self, job: ArchiveJob, proof: ArchiveProof) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select public.verify_cold_archive_object(
                    %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    job.job_id,
                    job.claim_token,
                    proof.content_sha256,
                    proof.object_path,
                    proof.object_sha256,
                    proof.object_bytes,
                    proof.row_count,
                ),
            )

    def complete_exact(self, job: ArchiveJob) -> int:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select public.complete_verified_cold_archive_job(%s, %s) as removed",
                (job.job_id, job.claim_token),
            )
            row = cursor.fetchone()
        return 0 if row is None else int(row["removed"])

    def mark_failed(self, job: ArchiveJob, reason: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                update public.cold_archive_jobs
                set status = 'failed', error_code = %s
                where job_id = %s and claim_token = %s and status <> 'completed'
                """,
                (reason[:120], job.job_id, job.claim_token),
            )

    def verified_objects(self, assets: list[str], from_ts: int, to_ts: int) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select j.asset, j.from_ts, j.to_ts, o.object_path, o.object_sha256,
                       o.content_sha256, o.row_count, o.object_bytes
                from public.cold_archive_objects o
                join public.cold_archive_jobs j on j.job_id = o.job_id
                where j.status = 'completed' and j.asset = any(%s)
                  and j.from_ts <= %s and j.to_ts > %s
                order by j.asset, j.from_ts, o.object_path
                """,
                (assets, to_ts, from_ts),
            )
            return list(cursor.fetchall())

    def all_verified_objects(self) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select j.asset, j.from_ts, j.to_ts, o.object_path, o.object_sha256,
                       o.content_sha256, o.row_count, o.object_bytes
                from public.cold_archive_objects o
                join public.cold_archive_jobs j on j.job_id = o.job_id
                where j.status = 'completed'
                order by j.asset, j.from_ts, o.object_path
                """
            )
            return list(cursor.fetchall())


def _archive_row(row: dict[str, Any]) -> ArchiveRow:
    return ArchiveRow(
        asset=str(row["asset"]),
        ts=int(row["ts"]),
        o=Decimal(row["o"]),
        h=Decimal(row["h"]),
        l=Decimal(row["l"]),
        c=Decimal(row["c"]),
        tick_vol=int(row["tick_vol"]),
        source=str(row["source"]),
        collected_at=int(row["collected_at"]),
        payout_observations=tuple(
            ArchivePayoutObservation(
                observed_at=int(item["observed_at"]),
                payout_pct=Decimal(str(item["payout_pct"])),
                source=str(item["source"]),
            )
            for item in row["payout_observations"]
        ),
    )
