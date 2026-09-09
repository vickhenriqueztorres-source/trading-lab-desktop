"""Point-in-time research datasets, snapshots and coverage gates (R-COL-3..9, R-RES-1)."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from primitives import Candle

from strategy_lab.archive import ArchiveRow, ColdArchiveReader, content_sha256
from strategy_lab.collect.sessions import in_session as default_in_session

MIN_COVERAGE = Decimal("0.95")
DATASET_SCHEMA_VERSION = 2
SUPPORTED_TIMEFRAMES = {60: "M1", 300: "M5", 900: "M15"}


class ResearchDatasetError(RuntimeError):
    pass


class DatasetOrigin(StrEnum):
    SUPABASE = "supabase"
    PARQUET = "parquet"
    SYNTHETIC = "synthetic"
    FIXTURE = "fixture"


@dataclass(frozen=True)
class CoverageEntry:
    asset: str
    timeframe_s: int
    from_ts: int
    to_ts: int
    present: int
    expected: int
    coverage: Decimal
    unresolved_in_session_gaps: int


@dataclass(frozen=True)
class DatasetQuality:
    present: int
    expected: int
    coverage: Decimal
    unresolved_in_session_gaps: int
    tick_volume_available: bool
    approval_eligible: bool


@dataclass(frozen=True)
class DatasetIdentity:
    schema_version: int
    origin: DatasetOrigin
    source: str
    asset: str
    timeframe_s: int
    from_ts: int
    to_ts: int


@dataclass(frozen=True)
class DatasetSnapshot:
    identity: DatasetIdentity
    quality: DatasetQuality
    fingerprint: str

    def public_evidence(self) -> dict[str, object]:
        return {
            "schema_version": self.identity.schema_version,
            "origin": self.identity.origin.value,
            "source": self.identity.source,
            "asset": self.identity.asset,
            "timeframe_s": self.identity.timeframe_s,
            "from_ts": self.identity.from_ts,
            "to_ts": self.identity.to_ts,
            "fingerprint": self.fingerprint,
            "quality": {
                "present": self.quality.present,
                "expected": self.quality.expected,
                "coverage": format(self.quality.coverage, "f"),
                "unresolved_in_session_gaps": self.quality.unresolved_in_session_gaps,
                "tick_volume_available": self.quality.tick_volume_available,
                "approval_eligible": self.quality.approval_eligible,
            },
        }


@dataclass(frozen=True)
class CandleBundle:
    snapshot: DatasetSnapshot
    candles: tuple[Candle, ...]


class ResearchDataset:
    def __init__(
        self,
        candles: Any,
        payouts: Any,
        gaps: Any | None = None,
        sessions: Any | None = None,
        *,
        origin: DatasetOrigin = DatasetOrigin.FIXTURE,
        source: str = "in-memory-fixture",
    ) -> None:
        self.candles = candles
        self.payouts = payouts
        self.gaps = gaps if gaps is not None else _pl().DataFrame()
        self.sessions = sessions if sessions is not None else _pl().DataFrame()
        self.origin = origin
        self.source = source

    @classmethod
    def from_rows(
        cls,
        candles: Iterable[Mapping[str, object]],
        payouts: Iterable[Mapping[str, object]],
        gaps: Iterable[Mapping[str, object]] = (),
        sessions: Iterable[Mapping[str, object]] = (),
        *,
        origin: DatasetOrigin = DatasetOrigin.FIXTURE,
        source: str = "in-memory-fixture",
    ) -> ResearchDataset:
        return cls(
            _pl().DataFrame(list(candles)),
            _pl().DataFrame(list(payouts)),
            _pl().DataFrame(list(gaps)),
            _pl().DataFrame(list(sessions)),
            origin=origin,
            source=source,
        )

    @classmethod
    def from_supabase(
        cls,
        db_url: str,
        assets: list[str],
        from_ts: int,
        to_ts: int,
        *,
        cold_reader: ColdArchiveReader | None = None,
    ) -> ResearchDataset:
        _validate_range(from_ts, to_ts)
        psycopg = importlib.import_module("psycopg")
        rows_factory = importlib.import_module("psycopg.rows").dict_row
        with (
            psycopg.connect(db_url, row_factory=rows_factory) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                    select asset, ts, o, h, l, c, tick_vol, source, collected_at
                    from public.candles
                    where asset = any(%s) and ts between %s and %s
                    order by asset, ts
                    """,
                (assets, from_ts, to_ts),
            )
            candles = list(cursor.fetchall())
            cursor.execute(
                """
                    select asset, observed_at, observed_at - observed_at %% 3600 as hour_ts,
                           payout_pct, 1 as samples, source
                    from public.payout_observations
                    where asset = any(%s)
                      and observed_at between %s and %s
                    order by asset, observed_at
                    """,
                (assets, from_ts - from_ts % 3600, to_ts),
            )
            payouts = list(cursor.fetchall())
            cursor.execute(
                """
                    select asset, from_ts, to_ts, in_session, resolved
                    from public.gaps
                    where asset = any(%s)
                      and from_ts <= %s
                      and to_ts >= %s
                    """,
                (assets, to_ts, from_ts),
            )
            gaps = list(cursor.fetchall())
            cursor.execute(
                """
                    select asset, weekday, open_min, close_min
                    from public.market_sessions
                    where asset = any(%s)
                    order by asset, weekday, open_min
                    """,
                (assets,),
            )
            sessions = list(cursor.fetchall())
            cursor.execute(
                """
                    select count(*) as object_count
                    from public.cold_archive_objects o
                    join public.cold_archive_jobs j on j.job_id = o.job_id
                    where j.status = 'completed' and j.asset = any(%s)
                      and j.from_ts <= %s and j.to_ts > %s
                    """,
                (assets, to_ts, from_ts),
            )
            archive_row = cursor.fetchone()
            cold_object_count = 0 if archive_row is None else int(archive_row["object_count"])
        if cold_object_count and cold_reader is None:
            raise ResearchDatasetError("RES_COLD_ARCHIVE_READER_REQUIRED")
        cold_rows = (
            []
            if cold_object_count == 0 or cold_reader is None
            else cold_reader.read(assets, from_ts, to_ts)
        )
        candles = _merge_hot_and_cold_candles(candles, cold_rows)
        payouts = _merge_hot_and_cold_payouts(payouts, cold_rows)
        cold_fingerprint = content_sha256(cold_rows) if cold_rows else "none"
        return cls.from_rows(
            candles,
            payouts,
            gaps,
            sessions,
            origin=DatasetOrigin.SUPABASE,
            source=f"supabase:hot+cold-v1:{cold_fingerprint}",
        )

    @classmethod
    def from_parquet(
        cls,
        candles_path: str,
        payouts_path: str,
        gaps_path: str | None = None,
        *,
        sessions_path: str | None = None,
    ) -> ResearchDataset:
        duckdb = importlib.import_module("duckdb")
        connection = duckdb.connect()
        try:
            candles = connection.execute("select * from read_parquet(?)", [candles_path]).pl()
            payouts = connection.execute("select * from read_parquet(?)", [payouts_path]).pl()
            gaps = (
                connection.execute("select * from read_parquet(?)", [gaps_path]).pl()
                if gaps_path is not None
                else _pl().DataFrame()
            )
            sessions = (
                connection.execute("select * from read_parquet(?)", [sessions_path]).pl()
                if sessions_path is not None
                else _pl().DataFrame()
            )
        finally:
            connection.close()
        source = (
            "parquet:"
            + hashlib.sha256(
                (
                    str(Path(candles_path).resolve()) + "|" + str(Path(payouts_path).resolve())
                ).encode()
            ).hexdigest()
        )
        return cls(
            candles,
            payouts,
            gaps,
            sessions,
            origin=DatasetOrigin.PARQUET,
            source=source,
        )

    def coverage(self, asset: str, from_ts: int, to_ts: int, *, timeframe_s: int = 60) -> Decimal:
        return self.coverage_entry(asset, from_ts, to_ts, timeframe_s=timeframe_s).coverage

    def coverage_entry(
        self, asset: str, from_ts: int, to_ts: int, *, timeframe_s: int = 60
    ) -> CoverageEntry:
        candles = self._aggregate(asset, timeframe_s, from_ts, to_ts)
        expected_starts = self._expected_bucket_starts(asset, timeframe_s, from_ts, to_ts)
        present = len(candles)
        expected = len(expected_starts)
        coverage = Decimal(present) / Decimal(expected) if expected else Decimal("0")
        return CoverageEntry(
            asset=asset,
            timeframe_s=timeframe_s,
            from_ts=from_ts,
            to_ts=to_ts,
            present=present,
            expected=expected,
            coverage=coverage,
            unresolved_in_session_gaps=self.unresolved_in_session_gaps(asset, from_ts, to_ts),
        )

    def refuse_if_coverage_below(
        self,
        asset: str,
        from_ts: int,
        to_ts: int,
        minimum: Decimal = MIN_COVERAGE,
        *,
        timeframe_s: int = 60,
    ) -> None:
        entry = self.coverage_entry(asset, from_ts, to_ts, timeframe_s=timeframe_s)
        if entry.coverage < minimum or entry.unresolved_in_session_gaps > 0:
            raise ResearchDatasetError("RES_COVERAGE_BELOW_MINIMUM")

    def unresolved_in_session_gaps(self, asset: str, from_ts: int, to_ts: int) -> int:
        if self.gaps.is_empty() or "in_session" not in self.gaps.columns:
            return 0
        return int(
            self.gaps.filter(
                (_pl().col("asset") == asset)
                & (_pl().col("in_session") == True)  # noqa: E712 - Polars expression API.
                & (_pl().col("resolved") == False)  # noqa: E712 - Polars expression API.
                & (_pl().col("from_ts") <= to_ts)
                & (_pl().col("to_ts") >= from_ts)
            ).height
        )

    def bundle_for(
        self,
        asset: str,
        timeframe_s: int,
        from_ts: int,
        to_ts: int,
        *,
        now_ts: int,
        require_approval_quality: bool = True,
    ) -> CandleBundle:
        _validate_range(from_ts, to_ts)
        _validate_timeframe(timeframe_s)
        cutoff_exclusive = now_ts // 60 * 60 - 60
        if to_ts >= cutoff_exclusive:
            raise ResearchDatasetError("RES_CURRENT_CANDLE_FORBIDDEN")
        candles = tuple(self._aggregate(asset, timeframe_s, from_ts, to_ts))
        entry = self.coverage_entry(asset, from_ts, to_ts, timeframe_s=timeframe_s)
        tick_volume_available = bool(candles) and all(
            candle.tick_vol is not None for candle in candles
        )
        approval_eligible = (
            entry.coverage >= MIN_COVERAGE
            and entry.unresolved_in_session_gaps == 0
            and bool(candles)
            and self.origin is not DatasetOrigin.FIXTURE
        )
        quality = DatasetQuality(
            present=entry.present,
            expected=entry.expected,
            coverage=entry.coverage,
            unresolved_in_session_gaps=entry.unresolved_in_session_gaps,
            tick_volume_available=tick_volume_available,
            approval_eligible=approval_eligible,
        )
        identity = DatasetIdentity(
            schema_version=DATASET_SCHEMA_VERSION,
            origin=self.origin,
            source=self.source,
            asset=asset,
            timeframe_s=timeframe_s,
            from_ts=from_ts,
            to_ts=to_ts,
        )
        fingerprint = _snapshot_fingerprint(identity, quality, candles, self._payout_rows(asset))
        snapshot = DatasetSnapshot(identity=identity, quality=quality, fingerprint=fingerprint)
        if require_approval_quality and not approval_eligible:
            raise ResearchDatasetError("RES_DATASET_NOT_APPROVAL_ELIGIBLE")
        return CandleBundle(snapshot=snapshot, candles=candles)

    def candles_for(
        self,
        asset: str,
        from_ts: int,
        to_ts: int,
        *,
        timeframe_s: int = 60,
    ) -> list[Candle]:
        """Compatibility reader; operational research must use ``bundle_for``."""
        return self._aggregate(asset, timeframe_s, from_ts, to_ts)

    def _aggregate(self, asset: str, timeframe_s: int, from_ts: int, to_ts: int) -> list[Candle]:
        _validate_range(from_ts, to_ts)
        _validate_timeframe(timeframe_s)
        if self.candles.is_empty() or "asset" not in self.candles.columns:
            return []
        raw = (
            self.candles.filter(
                (_pl().col("asset") == asset)
                & (_pl().col("ts") >= from_ts - timeframe_s + 60)
                & (_pl().col("ts") <= to_ts)
            )
            .sort("ts")
            .to_dicts()
        )
        by_ts: dict[int, Mapping[str, object]] = {}
        for row in raw:
            ts = _strict_int(row.get("ts"))
            if ts in by_ts:
                raise ResearchDatasetError("RES_DUPLICATE_CANDLE")
            by_ts[ts] = row
        result: list[Candle] = []
        for bucket_start in self._expected_bucket_starts(asset, timeframe_s, from_ts, to_ts):
            expected = tuple(range(bucket_start, bucket_start + timeframe_s, 60))
            if any(ts not in by_ts for ts in expected):
                continue
            rows = [by_ts[ts] for ts in expected]
            volumes = [_optional_int(row.get("tick_vol")) for row in rows]
            tick_vol = (
                None
                if any(value is None for value in volumes)
                else sum(value for value in volumes if value is not None)
            )
            result.append(
                Candle(
                    ts=bucket_start,
                    o=_decimal(rows[0]["o"]),
                    h=max(_decimal(row["h"]) for row in rows),
                    l=min(_decimal(row["l"]) for row in rows),
                    c=_decimal(rows[-1]["c"]),
                    tick_vol=tick_vol,
                )
            )
        return result

    def _expected_bucket_starts(
        self, asset: str, timeframe_s: int, from_ts: int, to_ts: int
    ) -> list[int]:
        first = ((from_ts + timeframe_s - 1) // timeframe_s) * timeframe_s
        last = to_ts - timeframe_s + 60
        if last < first:
            return []
        return [
            start
            for start in range(first, last + 1, timeframe_s)
            if all(self._in_session(asset, ts) for ts in range(start, start + timeframe_s, 60))
        ]

    def _in_session(self, asset: str, ts: int) -> bool:
        if not self.sessions.is_empty() and {"asset", "weekday", "open_min", "close_min"}.issubset(
            self.sessions.columns
        ):
            weekday = (ts // 86400 + 3) % 7
            minute = (ts % 86400) // 60
            matches = self.sessions.filter(
                (_pl().col("asset") == asset)
                & (_pl().col("weekday") == weekday)
                & (_pl().col("open_min") <= minute)
                & (_pl().col("close_min") > minute)
            )
            return bool(matches.height > 0)
        return default_in_session(asset, ts)

    def _payout_rows(self, asset: str) -> list[dict[str, object]]:
        if self.payouts.is_empty() or "asset" not in self.payouts.columns:
            return []
        rows = (
            self.payouts.filter(_pl().col("asset") == asset)
            .sort("observed_at" if "observed_at" in self.payouts.columns else "hour_ts")
            .to_dicts()
        )
        return cast(list[dict[str, object]], rows)


def coverage_report(
    dataset: ResearchDataset,
    assets: list[str],
    from_ts: int,
    to_ts: int,
    *,
    timeframe_s: int = 60,
) -> list[dict[str, object]]:
    return [
        {
            "asset": entry.asset,
            "timeframe_s": entry.timeframe_s,
            "from_ts": entry.from_ts,
            "to_ts": entry.to_ts,
            "present": entry.present,
            "expected": entry.expected,
            "coverage": format(entry.coverage, "f"),
            "unresolved_in_session_gaps": entry.unresolved_in_session_gaps,
            "accepted": entry.coverage >= MIN_COVERAGE and entry.unresolved_in_session_gaps == 0,
        }
        for entry in (
            dataset.coverage_entry(asset, from_ts, to_ts, timeframe_s=timeframe_s)
            for asset in assets
        )
    ]


def timeframe_seconds(value: str | int) -> int:
    if isinstance(value, int):
        _validate_timeframe(value)
        return value
    normalized = value.upper()
    result = {name: seconds for seconds, name in SUPPORTED_TIMEFRAMES.items()}.get(normalized)
    if result is None:
        raise ResearchDatasetError("RES_TIMEFRAME_UNSUPPORTED")
    return result


def synthetic_snapshot(
    candles: Iterable[Candle], *, asset: str, timeframe_s: int = 60, seed: int
) -> DatasetSnapshot:
    items = tuple(candles)
    if not items:
        raise ResearchDatasetError("RES_DATASET_EMPTY")
    identity = DatasetIdentity(
        schema_version=DATASET_SCHEMA_VERSION,
        origin=DatasetOrigin.SYNTHETIC,
        source=f"synthetic:seed:{seed}",
        asset=asset,
        timeframe_s=timeframe_s,
        from_ts=items[0].ts,
        to_ts=items[-1].ts,
    )
    quality = DatasetQuality(
        present=len(items),
        expected=len(items),
        coverage=Decimal("1"),
        unresolved_in_session_gaps=0,
        tick_volume_available=all(candle.tick_vol is not None for candle in items),
        approval_eligible=False,
    )
    return DatasetSnapshot(
        identity=identity,
        quality=quality,
        fingerprint=_snapshot_fingerprint(identity, quality, items, []),
    )


def _snapshot_fingerprint(
    identity: DatasetIdentity,
    quality: DatasetQuality,
    candles: Iterable[Candle],
    payouts: Iterable[Mapping[str, object]],
) -> str:
    payload = {
        "identity": {**asdict(identity), "origin": identity.origin.value},
        "quality": {**asdict(quality), "coverage": format(quality.coverage, "f")},
        "candles": [
            {
                "ts": candle.ts,
                "o": format(candle.o, "f"),
                "h": format(candle.h, "f"),
                "l": format(candle.l, "f"),
                "c": format(candle.c, "f"),
                "tick_vol": candle.tick_vol,
            }
            for candle in candles
        ],
        "payouts": [_canonical_mapping(row) for row in payouts],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_mapping(row: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): format(value, "f") if isinstance(value, Decimal) else value
        for key, value in sorted(row.items())
    }


def _validate_timeframe(timeframe_s: int) -> None:
    if type(timeframe_s) is not int or timeframe_s not in SUPPORTED_TIMEFRAMES:
        raise ResearchDatasetError("RES_TIMEFRAME_UNSUPPORTED")


def _validate_range(from_ts: int, to_ts: int) -> None:
    if (
        type(from_ts) is not int
        or type(to_ts) is not int
        or from_ts < 0
        or from_ts % 60
        or to_ts < from_ts
        or to_ts % 60
    ):
        raise ResearchDatasetError("RES_DATASET_RANGE_INVALID")


def _strict_int(value: object) -> int:
    if type(value) is not int:
        raise ResearchDatasetError("RES_DATASET_INTEGER_INVALID")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _strict_int(value)


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _pl() -> Any:
    return importlib.import_module("polars")


def _merge_hot_and_cold_candles(
    hot_rows: list[dict[str, object]], cold_rows: list[ArchiveRow]
) -> list[dict[str, object]]:
    merged: dict[tuple[str, int], dict[str, object]] = {
        (str(row["asset"]), _strict_int(row["ts"])): dict(row) for row in hot_rows
    }
    for archived in cold_rows:
        key = (archived.asset, archived.ts)
        candidate: dict[str, object] = {
            "asset": archived.asset,
            "ts": archived.ts,
            "o": archived.o,
            "h": archived.h,
            "l": archived.l,
            "c": archived.c,
            "tick_vol": archived.tick_vol,
            "source": archived.source,
            "collected_at": archived.collected_at,
        }
        existing = merged.get(key)
        if existing is not None and _canonical_candle_record(existing) != _canonical_candle_record(
            candidate
        ):
            raise ResearchDatasetError("RES_HOT_COLD_CONTENT_CONFLICT")
        merged[key] = candidate
    return [merged[key] for key in sorted(merged)]


def _merge_hot_and_cold_payouts(
    hot_rows: list[dict[str, object]], cold_rows: list[ArchiveRow]
) -> list[dict[str, object]]:
    merged: dict[tuple[str, int], dict[str, object]] = {
        (str(row["asset"]), _strict_int(row["observed_at"])): dict(row) for row in hot_rows
    }
    for archived in cold_rows:
        for observation in archived.payout_observations:
            key = (archived.asset, observation.observed_at)
            candidate: dict[str, object] = {
                "asset": archived.asset,
                "observed_at": observation.observed_at,
                "hour_ts": observation.observed_at - observation.observed_at % 3600,
                "payout_pct": observation.payout_pct,
                "samples": 1,
                "source": observation.source,
            }
            existing = merged.get(key)
            if existing is not None and _canonical_mapping(existing) != _canonical_mapping(
                candidate
            ):
                raise ResearchDatasetError("RES_HOT_COLD_PAYOUT_CONFLICT")
            merged[key] = candidate
    return [merged[key] for key in sorted(merged)]


def _canonical_candle_record(row: Mapping[str, object]) -> tuple[object, ...]:
    return (
        str(row["asset"]),
        _strict_int(row["ts"]),
        format(_decimal(row["o"]), "f"),
        format(_decimal(row["h"]), "f"),
        format(_decimal(row["l"]), "f"),
        format(_decimal(row["c"]), "f"),
        _strict_int(row["tick_vol"]),
        str(row["source"]),
        _strict_int(row["collected_at"]),
    )
