"""Postgres implementation for CAT-05 durable research evidence."""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from strategy_lab.research.evidence import (
    DatasetSnapshotRecord,
    EvidenceArtifact,
    EvidenceUnavailable,
    HoldoutReservation,
    ResearchAttemptRecord,
)


class PostgresEvidenceRepository:
    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url or os.environ.get("SUPABASE_DB_URL", "")
        if not self._db_url:
            raise EvidenceUnavailable("SUPABASE_DB_URL_REQUIRED")

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        psycopg = importlib.import_module("psycopg")
        with psycopg.connect(self._db_url) as connection:
            yield connection

    def record_dataset_snapshot(self, record: DatasetSnapshotRecord) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.dataset_snapshots(
                    fingerprint, origin, source, asset, timeframe_s, from_ts, to_ts,
                    present, expected, coverage, unresolved_in_session_gaps,
                    tick_volume_available, approval_eligible, public_evidence
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                on conflict (fingerprint) do nothing
                """,
                (
                    record.fingerprint,
                    record.origin,
                    record.source,
                    record.asset,
                    record.timeframe_s,
                    record.from_ts,
                    record.to_ts,
                    record.present,
                    record.expected,
                    _decimal(record.coverage),
                    record.unresolved_in_session_gaps,
                    record.tick_volume_available,
                    record.approval_eligible,
                    _json(record.public_evidence),
                ),
            )

    def reserve_holdout(self, reservation: HoldoutReservation) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.holdout_reservations(
                    reservation_id, dataset_fingerprint, asset, timeframe_s, from_ts, to_ts,
                    holdout_hash, reserved_by_run_id, state
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, 'sealed')
                on conflict (dataset_fingerprint, asset, timeframe_s, from_ts, to_ts)
                do nothing
                returning reservation_id
                """,
                (
                    reservation.reservation_id,
                    reservation.dataset_fingerprint,
                    reservation.asset,
                    reservation.timeframe_s,
                    reservation.from_ts,
                    reservation.to_ts,
                    reservation.holdout_hash,
                    reservation.reserved_by_run_id,
                ),
            )
            if cursor.fetchone() is not None:
                return
            cursor.execute(
                """
                select reservation_id, reserved_by_run_id, state
                from public.holdout_reservations
                where dataset_fingerprint = %s and asset = %s and timeframe_s = %s
                  and from_ts = %s and to_ts = %s
                """,
                (
                    reservation.dataset_fingerprint,
                    reservation.asset,
                    reservation.timeframe_s,
                    reservation.from_ts,
                    reservation.to_ts,
                ),
            )
            existing = cursor.fetchone()
            if existing is None or existing[2] != "rolled_back":
                raise EvidenceUnavailable("RES_HOLDOUT_ALREADY_RESERVED")

    def mark_holdout_opened(self, reservation_id: str, *, run_id: str, opened_at: int) -> None:
        self._set_holdout_state(
            reservation_id,
            run_id=run_id,
            target="opened",
            timestamp_column="opened_at",
            timestamp=opened_at,
            allowed=("sealed",),
        )

    def burn_holdout(self, reservation_id: str, *, run_id: str, burned_at: int) -> None:
        self._set_holdout_state(
            reservation_id,
            run_id=run_id,
            target="burned",
            timestamp_column="burned_at",
            timestamp=burned_at,
            allowed=("sealed", "opened"),
        )

    def rollback_holdout(self, reservation_id: str, *, run_id: str, reason: str) -> None:
        if not reason.strip():
            raise EvidenceUnavailable("RES_HOLDOUT_ROLLBACK_REASON_REQUIRED")
        self._set_holdout_state(
            reservation_id,
            run_id=run_id,
            target="rolled_back",
            timestamp_column="rolled_back_at",
            timestamp=None,
            allowed=("sealed",),
        )

    def burned_holdout_ranges(
        self, *, dataset_fingerprint: str, asset: str, timeframe_s: int
    ) -> tuple[tuple[int, int], ...]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select from_ts, to_ts
                from public.holdout_reservations
                where dataset_fingerprint = %s
                  and asset = %s
                  and timeframe_s = %s
                  and state = 'burned'
                """,
                (dataset_fingerprint, asset, timeframe_s),
            )
            return tuple((int(row[0]), int(row[1])) for row in cursor.fetchall())

    def record_attempt(self, record: ResearchAttemptRecord) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.research_attempts(
                    run_id, candidate_hash, family, asset, timeframe, params_canonical,
                    partition, method_version, seed, status, approval_state, counts,
                    dataset_fingerprint
                )
                values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb, %s)
                on conflict (run_id, candidate_hash, partition) do update
                set status = excluded.status,
                    approval_state = excluded.approval_state,
                    counts = excluded.counts
                """,
                (
                    record.run_id,
                    record.candidate_hash,
                    record.family,
                    record.asset,
                    record.timeframe,
                    _json(record.params_canonical),
                    record.partition,
                    record.method_version,
                    record.seed,
                    record.status,
                    record.approval_state,
                    _json(record.counts),
                    record.dataset_fingerprint,
                ),
            )

    def record_artifact(self, artifact: EvidenceArtifact) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.research_evidence_artifacts(
                    sha256, kind, storage_path, byte_count, run_id, candidate_hash
                )
                values (%s, %s, %s, %s, %s, %s)
                on conflict (sha256) do nothing
                """,
                (
                    artifact.sha256,
                    artifact.kind,
                    artifact.storage_path,
                    artifact.byte_count,
                    artifact.run_id,
                    artifact.candidate_hash,
                ),
            )

    def _set_holdout_state(
        self,
        reservation_id: str,
        *,
        run_id: str,
        target: str,
        timestamp_column: str,
        timestamp: int | None,
        allowed: tuple[str, ...],
    ) -> None:
        placeholders = ", ".join("%s" for _ in allowed)
        timestamp_sql = (
            f", {timestamp_column} = extract(epoch from now())::bigint"
            if timestamp is None
            else f", {timestamp_column} = %s"
        )
        params: tuple[object, ...]
        if timestamp is None:
            params = (target, reservation_id, run_id, *allowed)
        else:
            params = (target, timestamp, reservation_id, run_id, *allowed)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                update public.holdout_reservations
                set state = %s
                    {timestamp_sql}
                where reservation_id = %s
                  and reserved_by_run_id = %s
                  and state in ({placeholders})
                """,
                params,
            )
            if cursor.rowcount != 1:
                raise EvidenceUnavailable("RES_HOLDOUT_STATE_INVALID")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decimal(value: Decimal) -> str:
    return format(value, "f")
