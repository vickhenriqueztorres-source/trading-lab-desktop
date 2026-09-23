"""Unit tests for /healthz endpoint."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from apps.license_server.errors import ErrorCode


def test_health_with_real_db(pg_app: TestClient) -> None:
    """When PostgreSQL is connected, /healthz returns 200 with {'ok': True}."""
    response = pg_app.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_health_without_db_fails_closed(app_client: TestClient) -> None:
    """When PostgreSQL is disconnected, /healthz returns 503 AUTH_SERVICE_UNAVAILABLE."""
    response = app_client.get("/healthz")
    assert response.status_code == 503
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == ErrorCode.AUTH_SERVICE_UNAVAILABLE.value


def test_health_with_mocked_db(monkeypatch: pytest.MonkeyPatch, app_client: TestClient) -> None:
    """When DB query succeeds, /healthz returns 200 with {'ok': True}."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (1,)
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    @contextmanager
    def fake_with_conn() -> object:
        yield mock_conn

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setattr("apps.license_server.routes.health.with_conn", fake_with_conn)

    response = app_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
