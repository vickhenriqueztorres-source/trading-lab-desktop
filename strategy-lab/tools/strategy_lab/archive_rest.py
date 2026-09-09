"""Supabase REST archive repository for the local CAT-17 executor (R-HUB-7)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Any

from strategy_lab.archive import (
    ArchiveError,
    ArchiveJob,
    ArchivePayoutObservation,
    ArchiveProof,
    ArchiveRow,
    ArchiveStatus,
)


class SupabaseRestArchiveRepository:
    def __init__(self, base_url: str, service_role_key: str) -> None:
        if not base_url.startswith("https://") or not service_role_key:
            raise ArchiveError("ARCHIVE_REST_CONFIG_INVALID")
        self._base_url = base_url.rstrip("/")
        self._key = service_role_key

    def plan(self) -> str | None:
        value = self._request_json("POST", "/rest/v1/rpc/archive_old_candles", {})
        return value if isinstance(value, str) else None

    def claim(self, worker_id: str, lease_seconds: int) -> ArchiveJob | None:
        payload = self._request_json(
            "POST",
            "/rest/v1/rpc/claim_cold_archive_job",
            {"worker_id": worker_id, "lease_seconds": lease_seconds},
        )
        if not isinstance(payload, list) or not payload:
            return None
        row = _mapping(payload[0])
        return ArchiveJob(
            job_id=str(row["job_id"]),
            asset=str(row["asset"]),
            from_ts=_int(row["from_ts"]),
            to_ts=_int(row["to_ts"]),
            expected_count=_int(row["expected_count"]),
            claim_token=str(row["claim_token"]),
            status=ArchiveStatus(str(row["status"])),
        )

    def frozen_rows(self, job: ArchiveJob) -> list[ArchiveRow]:
        query = urllib.parse.urlencode(
            {
                "job_id": f"eq.{job.job_id}",
                "select": ("asset,ts,o,h,l,c,tick_vol,source,collected_at,payout_observations"),
                "order": "asset.asc,ts.asc",
            }
        )
        payload = self._request_json("GET", f"/rest/v1/cold_archive_job_rows?{query}")
        if not isinstance(payload, list):
            raise ArchiveError("ARCHIVE_REST_RESPONSE_INVALID")
        return [_archive_row(_mapping(item)) for item in payload]

    def mark_verified(self, job: ArchiveJob, proof: ArchiveProof) -> None:
        self._request_json(
            "POST",
            "/rest/v1/rpc/verify_cold_archive_object",
            {
                "target_job_id": job.job_id,
                "target_claim_token": job.claim_token,
                "target_snapshot_sha256": proof.content_sha256,
                "target_object_path": proof.object_path,
                "target_object_sha256": proof.object_sha256,
                "target_object_bytes": proof.object_bytes,
                "target_row_count": proof.row_count,
            },
        )

    def complete_exact(self, job: ArchiveJob) -> int:
        value = self._request_json(
            "POST",
            "/rest/v1/rpc/complete_verified_cold_archive_job",
            {"target_job_id": job.job_id, "target_claim_token": job.claim_token},
        )
        return _int(value)

    def mark_failed(self, job: ArchiveJob, reason: str) -> None:
        query = urllib.parse.urlencode(
            {
                "job_id": f"eq.{job.job_id}",
                "claim_token": f"eq.{job.claim_token}",
                "status": "neq.completed",
            }
        )
        self._request_json(
            "PATCH",
            f"/rest/v1/cold_archive_jobs?{query}",
            {"status": "failed", "error_code": reason[:120]},
        )

    def verified_objects(
        self, assets: list[str], from_ts: int, to_ts: int
    ) -> list[dict[str, object]]:
        asset_values = ",".join(assets)
        query = urllib.parse.urlencode(
            {
                "select": (
                    "object_path,object_sha256,content_sha256,row_count,object_bytes,"
                    "cold_archive_jobs!inner(asset,from_ts,to_ts,status)"
                ),
                "cold_archive_jobs.status": "eq.completed",
                "cold_archive_jobs.asset": f"in.({asset_values})",
                "cold_archive_jobs.from_ts": f"lte.{to_ts}",
                "cold_archive_jobs.to_ts": f"gt.{from_ts}",
                "order": "object_path.asc",
            }
        )
        return self._object_records(
            self._request_json("GET", f"/rest/v1/cold_archive_objects?{query}")
        )

    def all_verified_objects(self) -> list[dict[str, object]]:
        query = urllib.parse.urlencode(
            {
                "select": (
                    "object_path,object_sha256,content_sha256,row_count,object_bytes,"
                    "cold_archive_jobs!inner(asset,from_ts,to_ts,status)"
                ),
                "cold_archive_jobs.status": "eq.completed",
                "order": "object_path.asc",
            }
        )
        return self._object_records(
            self._request_json("GET", f"/rest/v1/cold_archive_objects?{query}")
        )

    def _object_records(self, payload: object) -> list[dict[str, object]]:
        if not isinstance(payload, list):
            raise ArchiveError("ARCHIVE_REST_RESPONSE_INVALID")
        result: list[dict[str, object]] = []
        for item in payload:
            row = _mapping(item)
            job = _mapping(row["cold_archive_jobs"])
            result.append(
                {
                    "asset": str(job["asset"]),
                    "from_ts": _int(job["from_ts"]),
                    "to_ts": _int(job["to_ts"]),
                    "object_path": str(row["object_path"]),
                    "object_sha256": str(row["object_sha256"]),
                    "content_sha256": str(row["content_sha256"]),
                    "row_count": _int(row["row_count"]),
                    "object_bytes": _int(row["object_bytes"]),
                }
            )
        return result

    def _request_json(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> object:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        request = urllib.request.Request(
            self._base_url + path,
            data=data,
            method=method,
            headers={
                "apikey": self._key,
                "authorization": f"Bearer {self._key}",
                "content-type": "application/json",
                "prefer": "return=representation",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                payload = response.read()
        except urllib.error.HTTPError as error:
            raise ArchiveError(_http_error_code(error)) from error
        except OSError as error:
            raise ArchiveError("ARCHIVE_REST_REQUEST_FAILED") from error
        if not payload:
            return None
        try:
            return json.loads(payload)
        except (TypeError, ValueError) as error:
            raise ArchiveError("ARCHIVE_REST_RESPONSE_INVALID") from error


def _archive_row(row: dict[str, Any]) -> ArchiveRow:
    observations = row["payout_observations"]
    if not isinstance(observations, list):
        raise ArchiveError("ARCHIVE_REST_RESPONSE_INVALID")
    return ArchiveRow(
        asset=str(row["asset"]),
        ts=_int(row["ts"]),
        o=Decimal(str(row["o"])),
        h=Decimal(str(row["h"])),
        l=Decimal(str(row["l"])),
        c=Decimal(str(row["c"])),
        tick_vol=_int(row["tick_vol"]),
        source=str(row["source"]),
        collected_at=_int(row["collected_at"]),
        payout_observations=tuple(
            ArchivePayoutObservation(
                observed_at=_int(_mapping(item)["observed_at"]),
                payout_pct=Decimal(str(_mapping(item)["payout_pct"])),
                source=str(_mapping(item)["source"]),
            )
            for item in observations
        ),
    )


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ArchiveError("ARCHIVE_REST_RESPONSE_INVALID")
    return value


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ArchiveError("ARCHIVE_REST_RESPONSE_INVALID")
    return int(value)


def _http_error_code(error: urllib.error.HTTPError) -> str:
    detail = "UNKNOWN"
    try:
        document = json.loads(error.read())
        if isinstance(document, dict):
            candidate = document.get("code", "")
            if isinstance(candidate, str):
                sanitized = "".join(
                    character for character in candidate.upper() if character.isalnum()
                )
                if sanitized:
                    detail = sanitized[:32]
    except (OSError, TypeError, ValueError):
        pass
    return f"ARCHIVE_REST_HTTP_{error.code}_{detail}"
