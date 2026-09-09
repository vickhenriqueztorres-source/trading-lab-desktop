from __future__ import annotations

import json
from typing import Any

from strategy_lab.retention_rest import SupabaseRetentionInventory


class Response:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_storage_inventory_uses_post_with_explicit_empty_prefix(monkeypatch: Any) -> None:
    received: dict[str, object] = {}

    def fake_urlopen(request: Any, timeout: int) -> Response:
        received["method"] = request.method
        received["url"] = request.full_url
        received["body"] = json.loads(request.data.decode("utf-8"))
        assert timeout == 20
        return Response([])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    repository = SupabaseRetentionInventory(
        "https://example.test", "service-key", "project", "staging"
    )
    snapshot = repository.snapshot()

    assert snapshot.items == ()
    assert received == {
        "method": "POST",
        "url": "https://example.test/storage/v1/object/list/manifests",
        "body": {
            "prefix": "",
            "limit": 1000,
            "offset": 0,
            "sortBy": {"column": "name", "order": "asc"},
        },
    }
