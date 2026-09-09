"""Read-only Supabase inventory and exact object deletion for CAT-18.

The adapter deliberately exposes only an exact path delete.  It does not implement SQL
wildcards, cascade deletes, or a best-effort fallback.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any, cast

from strategy_lab.retention import (
    CleanupTarget,
    InventoryItem,
    InventorySnapshot,
    RetentionCategory,
    RetentionError,
)


class SupabaseRetentionInventory:
    def __init__(self, base_url: str, service_key: str, project_ref: str, environment: str) -> None:
        if not service_key or not project_ref or environment not in {"staging", "production"}:
            raise RetentionError("RETENTION_REMOTE_CONFIG_INVALID")
        self.base_url = base_url.rstrip("/")
        self.service_key = service_key
        self.project_ref = project_ref
        self.environment = environment

    def snapshot(self) -> InventorySnapshot:
        objects = self._post_json(
            "/storage/v1/object/list/manifests",
            {
                "prefix": "",
                "limit": 1000,
                "offset": 0,
                "sortBy": {"column": "name", "order": "asc"},
            },
        )
        items: list[InventoryItem] = []
        for raw in objects if isinstance(objects, list) else []:
            if not isinstance(raw, dict):
                continue
            raw_data = cast(dict[str, object], raw)
            if not isinstance(raw_data.get("name"), str):
                continue
            name = raw_data["name"]
            metadata_value = raw_data.get("metadata")
            metadata = (
                cast(dict[str, object], metadata_value) if isinstance(metadata_value, dict) else {}
            )
            size_value = metadata.get("size", 0)
            size = int(str(size_value)) if str(size_value).isdigit() else 0
            created_at = _parse_timestamp(raw_data.get("created_at"))
            sha = str(metadata.get("sha256", "0" * 64)).lower()
            if len(sha) != 64 or any(char not in "0123456789abcdef" for char in sha):
                sha = "0" * 64
            items.append(
                InventoryItem(
                    target_id=f"storage:manifests/{name}",
                    category=RetentionCategory.PUBLICATION_ORPHAN,
                    size_bytes=size,
                    created_at=created_at,
                    owner=str(raw_data.get("owner_id") or "storage"),
                    content_sha256=sha,
                    # Publication references and backup proofs are deliberately absent until
                    # the control-plane tables prove both; this keeps the plan fail-closed.
                    metadata=(("bucket", "manifests"), ("name", str(name))),
                )
            )
        now = int(datetime.now(UTC).timestamp())
        return InventorySnapshot(self.project_ref, self.environment, now, tuple(items))

    def delete_exact(self, target: CleanupTarget) -> None:
        prefix = "storage:manifests/"
        if (
            not target.target_id.startswith(prefix)
            or "*" in target.target_id
            or "?" in target.target_id
        ):
            raise RetentionError("RETENTION_TARGET_NOT_EXACT")
        name = target.target_id[len(prefix) :]
        if not name or "/" in name and name.endswith("/"):
            raise RetentionError("RETENTION_TARGET_NOT_EXACT")
        encoded = urllib.parse.quote(name, safe="")
        self._request("DELETE", f"/storage/v1/object/manifests/{encoded}")

    def _post_json(self, path: str, payload: dict[str, object]) -> Any:
        response = self._request(
            "POST",
            path,
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        )
        return json.loads(response.decode("utf-8"))

    def _request(self, method: str, path: str, body: bytes | None = None) -> bytes:
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "apikey": self.service_key,
                "Authorization": f"Bearer {self.service_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return cast(bytes, response.read())
        except urllib.error.HTTPError as exc:
            raise RetentionError(f"RETENTION_REMOTE_HTTP_{exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RetentionError("RETENTION_REMOTE_REQUEST_FAILED") from exc


def _parse_timestamp(value: object) -> int:
    if not isinstance(value, str):
        return 0
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).timestamp())
    except (ValueError, OverflowError):
        return 0
