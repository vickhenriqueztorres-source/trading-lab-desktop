"""Verified cold-archive executor (CAT-17, R-HUB-7, R-OPS-1).

The executor never authorizes deletion from an upload response.  It uploads an immutable
Parquet object, downloads it again, verifies bytes and logical content, records that proof,
and only then asks the repository to delete the exact unchanged primary keys.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Protocol

ARCHIVE_SCHEMA_VERSION = 1
PRICE_QUANTUM = Decimal("0.00000001")
PAYOUT_QUANTUM = Decimal("0.01")


class ArchiveError(RuntimeError):
    """Fail-closed archive error carrying a stable reason code."""


class ArchiveStatus(StrEnum):
    REQUESTED = "requested"
    CLAIMED = "claimed"
    UPLOADED = "uploaded"
    VERIFIED = "verified"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ArchivePayoutObservation:
    observed_at: int
    payout_pct: Decimal
    source: str

    def __post_init__(self) -> None:
        if (
            self.observed_at < 0
            or not Decimal("0") <= self.payout_pct <= Decimal("100")
            or not self.source
        ):
            raise ArchiveError("ARCHIVE_PAYOUT_INVALID")


@dataclass(frozen=True)
class ArchiveRow:
    asset: str
    ts: int
    o: Decimal
    h: Decimal
    l: Decimal  # noqa: E741 - canonical OHLC field name in the public archive schema.
    c: Decimal
    tick_vol: int
    source: str
    collected_at: int
    payout_observations: tuple[ArchivePayoutObservation, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.asset
            or self.ts < 0
            or self.ts % 60
            or self.tick_vol < 0
            or self.collected_at < 0
            or self.l > min(self.o, self.c)
            or max(self.o, self.c) > self.h
        ):
            raise ArchiveError("ARCHIVE_ROW_INVALID")
        if any(not self.ts <= item.observed_at < self.ts + 60 for item in self.payout_observations):
            raise ArchiveError("ARCHIVE_PAYOUT_OUTSIDE_CANDLE")


@dataclass(frozen=True)
class ArchiveJob:
    job_id: str
    asset: str
    from_ts: int
    to_ts: int
    expected_count: int
    claim_token: str
    status: ArchiveStatus = ArchiveStatus.CLAIMED


@dataclass(frozen=True)
class ArchiveProof:
    content_sha256: str
    object_path: str
    object_sha256: str
    object_bytes: int
    row_count: int
    compression_ratio: Decimal


@dataclass(frozen=True)
class ArchiveRunReport:
    status: str
    job_id: str | None
    object_path: str | None
    row_count: int
    deleted_count: int
    content_sha256: str | None
    object_sha256: str | None
    compression_ratio: str | None
    reason: str | None = None


class ArchiveRepository(Protocol):
    def claim(self, worker_id: str, lease_seconds: int) -> ArchiveJob | None: ...
    def frozen_rows(self, job: ArchiveJob) -> list[ArchiveRow]: ...
    def mark_verified(self, job: ArchiveJob, proof: ArchiveProof) -> None: ...
    def complete_exact(self, job: ArchiveJob) -> int: ...
    def mark_failed(self, job: ArchiveJob, reason: str) -> None: ...


class OperationalArchiveRepository(ArchiveRepository, Protocol):
    def plan(self) -> str | None: ...
    def all_verified_objects(self) -> list[dict[str, object]]: ...


class ArchiveObjectStore(Protocol):
    def put_immutable(self, path: str, payload: bytes) -> None: ...
    def get(self, path: str) -> bytes: ...


class ArchiveObjectIndex(Protocol):
    def verified_objects(
        self, assets: list[str], from_ts: int, to_ts: int
    ) -> list[dict[str, object]]: ...


class ColdArchiveReader(Protocol):
    def read(self, assets: list[str], from_ts: int, to_ts: int) -> list[ArchiveRow]: ...


def content_sha256(rows: Iterable[ArchiveRow]) -> str:
    canonical = json.dumps(
        [_canonical_row(row) for row in sorted(rows, key=lambda item: (item.asset, item.ts))],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def encode_parquet(rows: list[ArchiveRow]) -> tuple[bytes, Decimal]:
    if not rows:
        raise ArchiveError("ARCHIVE_EMPTY_SLICE")
    _validate_unique_rows(rows)
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    schema = pa.schema(
        [
            pa.field("asset", pa.string(), nullable=False),
            pa.field("ts", pa.int64(), nullable=False),
            pa.field("o", pa.decimal128(18, 8), nullable=False),
            pa.field("h", pa.decimal128(18, 8), nullable=False),
            pa.field("l", pa.decimal128(18, 8), nullable=False),
            pa.field("c", pa.decimal128(18, 8), nullable=False),
            pa.field("tick_vol", pa.int32(), nullable=False),
            pa.field("source", pa.string(), nullable=False),
            pa.field("collected_at", pa.int64(), nullable=False),
            pa.field(
                "payout_observations",
                pa.list_(
                    pa.struct(
                        [
                            pa.field("observed_at", pa.int64(), nullable=False),
                            pa.field("payout_pct", pa.decimal128(5, 2), nullable=False),
                            pa.field("source", pa.string(), nullable=False),
                        ]
                    )
                ),
                nullable=False,
            ),
        ],
        metadata={b"strategy_lab_archive_schema_version": b"1"},
    )
    ordered = sorted(rows, key=lambda item: (item.asset, item.ts))
    table = pa.Table.from_pydict(
        {
            "asset": [row.asset for row in ordered],
            "ts": [row.ts for row in ordered],
            "o": [row.o.quantize(PRICE_QUANTUM) for row in ordered],
            "h": [row.h.quantize(PRICE_QUANTUM) for row in ordered],
            "l": [row.l.quantize(PRICE_QUANTUM) for row in ordered],
            "c": [row.c.quantize(PRICE_QUANTUM) for row in ordered],
            "tick_vol": [row.tick_vol for row in ordered],
            "source": [row.source for row in ordered],
            "collected_at": [row.collected_at for row in ordered],
            "payout_observations": [
                [
                    {
                        "observed_at": item.observed_at,
                        "payout_pct": item.payout_pct.quantize(PAYOUT_QUANTUM),
                        "source": item.source,
                    }
                    for item in row.payout_observations
                ]
                for row in ordered
            ],
        },
        schema=schema,
    )
    output = io.BytesIO()
    pq.write_table(table, output, compression="zstd", version="2.6")
    payload = output.getvalue()
    logical_bytes = json.dumps(
        [_canonical_row(row) for row in ordered],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    ratio = Decimal(len(payload)) / Decimal(len(logical_bytes))
    return payload, ratio


def decode_and_verify_parquet(payload: bytes, expected_rows: list[ArchiveRow]) -> list[ArchiveRow]:
    decoded = decode_parquet(payload)
    if len(decoded) != len(expected_rows) or content_sha256(decoded) != content_sha256(
        expected_rows
    ):
        raise ArchiveError("ARCHIVE_CONTENT_MISMATCH")
    return decoded


def decode_parquet(payload: bytes) -> list[ArchiveRow]:
    if not payload:
        raise ArchiveError("ARCHIVE_OBJECT_EMPTY")
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    try:
        table = pq.read_table(pa.BufferReader(payload))
    except Exception as error:
        raise ArchiveError("ARCHIVE_OBJECT_CORRUPT") from error
    metadata = table.schema.metadata or {}
    if metadata.get(b"strategy_lab_archive_schema_version") != str(ARCHIVE_SCHEMA_VERSION).encode():
        raise ArchiveError("ARCHIVE_SCHEMA_MISMATCH")
    required = {
        "asset",
        "ts",
        "o",
        "h",
        "l",
        "c",
        "tick_vol",
        "source",
        "collected_at",
        "payout_observations",
    }
    if set(table.column_names) != required:
        raise ArchiveError("ARCHIVE_SCHEMA_MISMATCH")
    decoded = [
        ArchiveRow(
            asset=str(item["asset"]),
            ts=int(item["ts"]),
            o=Decimal(item["o"]),
            h=Decimal(item["h"]),
            l=Decimal(item["l"]),
            c=Decimal(item["c"]),
            tick_vol=int(item["tick_vol"]),
            source=str(item["source"]),
            collected_at=int(item["collected_at"]),
            payout_observations=tuple(
                ArchivePayoutObservation(
                    observed_at=int(observation["observed_at"]),
                    payout_pct=Decimal(observation["payout_pct"]),
                    source=str(observation["source"]),
                )
                for observation in item["payout_observations"]
            ),
        )
        for item in table.to_pylist()
    ]
    _validate_unique_rows(decoded)
    return decoded


def object_path(job: ArchiveJob, snapshot_sha256: str) -> str:
    return f"parquet/{job.asset}/{job.from_ts:010d}-{job.to_ts:010d}-{snapshot_sha256[:16]}.parquet"


class ArchiveExecutor:
    def __init__(
        self,
        repository: ArchiveRepository,
        object_store: ArchiveObjectStore,
        *,
        worker_id: str,
        lease_seconds: int = 900,
    ) -> None:
        if not worker_id or lease_seconds < 60:
            raise ArchiveError("ARCHIVE_EXECUTOR_CONFIG_INVALID")
        self._repository = repository
        self._object_store = object_store
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds

    def run_once(self) -> ArchiveRunReport:
        job = self._repository.claim(self._worker_id, self._lease_seconds)
        if job is None:
            return ArchiveRunReport("idle", None, None, 0, 0, None, None, None)
        try:
            rows = self._repository.frozen_rows(job)
            if len(rows) != job.expected_count or any(row.asset != job.asset for row in rows):
                raise ArchiveError("ARCHIVE_FROZEN_SLICE_MISMATCH")
            snapshot = content_sha256(rows)
            payload, compression = encode_parquet(rows)
            path = object_path(job, snapshot)
            self._object_store.put_immutable(path, payload)
            downloaded = self._object_store.get(path)
            object_hash = hashlib.sha256(downloaded).hexdigest()
            if object_hash != hashlib.sha256(payload).hexdigest():
                raise ArchiveError("ARCHIVE_OBJECT_HASH_MISMATCH")
            decode_and_verify_parquet(downloaded, rows)
            proof = ArchiveProof(
                content_sha256=snapshot,
                object_path=path,
                object_sha256=object_hash,
                object_bytes=len(downloaded),
                row_count=len(rows),
                compression_ratio=compression,
            )
            self._repository.mark_verified(job, proof)
            deleted = self._repository.complete_exact(job)
            if deleted != len(rows):
                raise ArchiveError("ARCHIVE_EXACT_DELETE_ABORTED")
            return ArchiveRunReport(
                "completed",
                job.job_id,
                path,
                len(rows),
                deleted,
                snapshot,
                object_hash,
                format(compression, "f"),
            )
        except ArchiveError as error:
            self._repository.mark_failed(job, str(error))
            return ArchiveRunReport("failed", job.job_id, None, 0, 0, None, None, None, str(error))


@dataclass
class MemoryObjectStore:
    objects: dict[str, bytes]

    def put_immutable(self, path: str, payload: bytes) -> None:
        existing = self.objects.get(path)
        if existing is not None and existing != payload:
            raise ArchiveError("ARCHIVE_IMMUTABLE_OBJECT_CONFLICT")
        self.objects[path] = payload

    def get(self, path: str) -> bytes:
        try:
            return self.objects[path]
        except KeyError as error:
            raise ArchiveError("ARCHIVE_OBJECT_NOT_FOUND") from error


class SupabaseStorage:
    """Minimal private Storage client using only the authorized standard-library stack."""

    def __init__(self, base_url: str, service_role_key: str, *, bucket: str = "parquet") -> None:
        if not base_url.startswith("https://") or not service_role_key or not bucket:
            raise ArchiveError("ARCHIVE_STORAGE_CONFIG_INVALID")
        self._base_url = base_url.rstrip("/")
        self._key = service_role_key
        self._bucket = bucket

    def put_immutable(self, path: str, payload: bytes) -> None:
        # No x-upsert header: an existing immutable path must produce a conflict.
        request = urllib.request.Request(
            self._url(path),
            data=payload,
            method="POST",
            headers={**self._headers(), "content-type": "application/vnd.apache.parquet"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                if response.status not in {200, 201}:
                    raise ArchiveError("ARCHIVE_STORAGE_UPLOAD_FAILED")
        except urllib.error.HTTPError as error:
            error_code = _storage_http_error("UPLOAD", error)
            if error.code == 409 or (error.code == 400 and error_code.endswith("_DUPLICATE")):
                if self.get(path) == payload:
                    return
                raise ArchiveError("ARCHIVE_IMMUTABLE_OBJECT_CONFLICT") from error
            raise ArchiveError(error_code) from error
        except OSError as error:
            raise ArchiveError("ARCHIVE_STORAGE_UPLOAD_FAILED") from error

    def get(self, path: str) -> bytes:
        request = urllib.request.Request(self._url(path), method="GET", headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                return bytes(response.read())
        except urllib.error.HTTPError as error:
            raise ArchiveError(_storage_http_error("DOWNLOAD", error)) from error
        except OSError as error:
            raise ArchiveError("ARCHIVE_STORAGE_DOWNLOAD_FAILED") from error

    def _url(self, path: str) -> str:
        encoded = urllib.parse.quote(path, safe="/")
        return f"{self._base_url}/storage/v1/object/{self._bucket}/{encoded}"

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._key}", "apikey": self._key}


class VerifiedColdArchiveReader:
    """Read only completed, indexed objects and re-check every recorded proof."""

    def __init__(self, index: ArchiveObjectIndex, object_store: ArchiveObjectStore) -> None:
        self._index = index
        self._object_store = object_store

    def read(self, assets: list[str], from_ts: int, to_ts: int) -> list[ArchiveRow]:
        rows: list[ArchiveRow] = []
        for record in self._index.verified_objects(assets, from_ts, to_ts):
            path = str(record["object_path"])
            payload = self._object_store.get(path)
            if len(payload) != _record_int(record, "object_bytes"):
                raise ArchiveError("ARCHIVE_OBJECT_SIZE_MISMATCH")
            if hashlib.sha256(payload).hexdigest() != str(record["object_sha256"]):
                raise ArchiveError("ARCHIVE_OBJECT_HASH_MISMATCH")
            decoded = decode_parquet(payload)
            if len(decoded) != _record_int(record, "row_count"):
                raise ArchiveError("ARCHIVE_OBJECT_COUNT_MISMATCH")
            if content_sha256(decoded) != str(record["content_sha256"]):
                raise ArchiveError("ARCHIVE_CONTENT_MISMATCH")
            rows.extend(
                row for row in decoded if row.asset in assets and from_ts <= row.ts <= to_ts
            )
        _validate_unique_rows(rows)
        return sorted(rows, key=lambda item: (item.asset, item.ts))


class EnvironmentColdArchiveReader:
    """Delay Storage credential validation until an archived object is actually needed."""

    def __init__(self, index: ArchiveObjectIndex) -> None:
        self._index = index

    def read(self, assets: list[str], from_ts: int, to_ts: int) -> list[ArchiveRow]:
        return VerifiedColdArchiveReader(self._index, storage_from_env()).read(
            assets, from_ts, to_ts
        )


def backup_verified_objects(
    records: list[dict[str, object]],
    object_store: ArchiveObjectStore,
    target_dir: Path,
) -> Path:
    """Back up verified Storage objects and seal a local inventory (R-OPS-1)."""
    object_dir = target_dir / "cold-objects"
    object_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    for record in records:
        path = str(record["object_path"])
        expected_hash = str(record["object_sha256"])
        payload = object_store.get(path)
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise ArchiveError("ARCHIVE_BACKUP_HASH_MISMATCH")
        if len(payload) != _record_int(record, "object_bytes"):
            raise ArchiveError("ARCHIVE_BACKUP_SIZE_MISMATCH")
        decoded = decode_parquet(payload)
        if len(decoded) != _record_int(record, "row_count"):
            raise ArchiveError("ARCHIVE_BACKUP_COUNT_MISMATCH")
        if content_sha256(decoded) != str(record["content_sha256"]):
            raise ArchiveError("ARCHIVE_BACKUP_CONTENT_MISMATCH")
        local_name = f"{expected_hash}.parquet"
        local_path = object_dir / local_name
        if local_path.exists() and local_path.read_bytes() != payload:
            raise ArchiveError("ARCHIVE_BACKUP_LOCAL_CONFLICT")
        if not local_path.exists():
            local_path.write_bytes(payload)
        entries.append(
            {
                "content_sha256": str(record["content_sha256"]),
                "local_name": local_name,
                "object_bytes": len(payload),
                "object_path": path,
                "object_sha256": expected_hash,
                "row_count": len(decoded),
            }
        )
    manifest_payload = json.dumps(
        {"schema_version": 1, "objects": entries},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    manifest_path = target_dir / "cold-objects.manifest.json"
    manifest_path.write_bytes(manifest_payload)
    manifest_path.with_suffix(".json.sha256").write_text(
        hashlib.sha256(manifest_payload).hexdigest() + "\n", encoding="ascii"
    )
    return manifest_path


def restore_verified_objects(
    manifest_path: Path,
    destination: ArchiveObjectStore,
) -> int:
    """Restore into an isolated destination and re-download every object for proof."""
    payload = manifest_path.read_bytes()
    checksum_path = manifest_path.with_suffix(".json.sha256")
    expected_manifest_hash = checksum_path.read_text(encoding="ascii").strip()
    if hashlib.sha256(payload).hexdigest() != expected_manifest_hash:
        raise ArchiveError("ARCHIVE_BACKUP_MANIFEST_HASH_MISMATCH")
    document = json.loads(payload)
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ArchiveError("ARCHIVE_BACKUP_MANIFEST_INVALID")
    records = document.get("objects")
    if not isinstance(records, list):
        raise ArchiveError("ARCHIVE_BACKUP_MANIFEST_INVALID")
    restored = 0
    for unknown_record in records:
        if not isinstance(unknown_record, dict):
            raise ArchiveError("ARCHIVE_BACKUP_MANIFEST_INVALID")
        record: dict[str, object] = unknown_record
        local_name = str(record.get("local_name", ""))
        if Path(local_name).name != local_name or not local_name.endswith(".parquet"):
            raise ArchiveError("ARCHIVE_BACKUP_PATH_INVALID")
        object_payload = (manifest_path.parent / "cold-objects" / local_name).read_bytes()
        expected_hash = str(record["object_sha256"])
        if hashlib.sha256(object_payload).hexdigest() != expected_hash:
            raise ArchiveError("ARCHIVE_BACKUP_HASH_MISMATCH")
        decoded = decode_parquet(object_payload)
        if len(decoded) != _record_int(record, "row_count"):
            raise ArchiveError("ARCHIVE_BACKUP_COUNT_MISMATCH")
        if content_sha256(decoded) != str(record["content_sha256"]):
            raise ArchiveError("ARCHIVE_BACKUP_CONTENT_MISMATCH")
        object_path_value = str(record["object_path"])
        destination.put_immutable(object_path_value, object_payload)
        if hashlib.sha256(destination.get(object_path_value)).hexdigest() != expected_hash:
            raise ArchiveError("ARCHIVE_RESTORE_VERIFICATION_FAILED")
        restored += 1
    return restored


def storage_from_env() -> SupabaseStorage:
    return SupabaseStorage(
        os.environ.get("SUPABASE_URL", ""),
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
    )


def _validate_unique_rows(rows: Iterable[ArchiveRow]) -> None:
    keys: set[tuple[str, int]] = set()
    for row in rows:
        key = (row.asset, row.ts)
        if key in keys:
            raise ArchiveError("ARCHIVE_DUPLICATE_PRIMARY_KEY")
        keys.add(key)


def _record_int(record: dict[str, object], key: str) -> int:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ArchiveError("ARCHIVE_INDEX_INVALID")
    return int(value)


def _storage_http_error(operation: str, error: urllib.error.HTTPError) -> str:
    detail = "UNKNOWN"
    try:
        document = json.loads(error.read())
        if isinstance(document, dict):
            candidate = document.get("error", document.get("code", ""))
            if isinstance(candidate, str):
                sanitized = "".join(
                    character for character in candidate.upper() if character.isalnum()
                )
                if sanitized:
                    detail = sanitized[:40]
    except (OSError, TypeError, ValueError):
        pass
    return f"ARCHIVE_STORAGE_{operation}_HTTP_{error.code}_{detail}"


def _canonical_row(row: ArchiveRow) -> dict[str, object]:
    return {
        "asset": row.asset,
        "c": format(row.c.quantize(PRICE_QUANTUM), "f"),
        "collected_at": row.collected_at,
        "h": format(row.h.quantize(PRICE_QUANTUM), "f"),
        "l": format(row.l.quantize(PRICE_QUANTUM), "f"),
        "o": format(row.o.quantize(PRICE_QUANTUM), "f"),
        "payout_observations": [
            {
                "observed_at": item.observed_at,
                "payout_pct": format(item.payout_pct.quantize(PAYOUT_QUANTUM), "f"),
                "source": item.source,
            }
            for item in row.payout_observations
        ],
        "source": row.source,
        "tick_vol": row.tick_vol,
        "ts": row.ts,
    }


def clone_with_price(row: ArchiveRow, *, c: Decimal) -> ArchiveRow:
    """Test/support helper that preserves validation while modeling a late correction."""
    return replace(row, c=c, h=max(row.h, c), l=min(row.l, c))
