"""Test fixtures for Trading Lab License Server unit tests."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from apps.license_server.db import close_pool, init_pool, run_migrations
from apps.license_server.keys import init_keys_in_db, load_signing_key
from apps.license_server.main import create_app
from apps.license_server.ratelimit import get_rate_limiter
from apps.license_server.settings import Settings


@pytest.fixture(autouse=True)
def reset_limiter() -> None:
    """Reset the in-memory rate limiter before each test."""
    get_rate_limiter().reset()


@pytest.fixture
def test_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Return in-memory test settings."""
    settings = Settings(
        environment="test",
        database_url=os.environ.get("TEST_DATABASE_URL", "").strip(),
        license_signing_key_pem=None,
        license_signing_key_id="tl-test-key",
        admin_email="admin@test.invalid",
        admin_session_secret="test-session-secret-at-least-32-chars-long",
        mail_provider="console",
        resend_api_key=None,
        mail_from="Trading Lab Test <test@tradinglab.app>",
        public_base_url="https://test-licencias.tradinglab.app",
        support_contact_url="https://t.me/test_support",
        renew_url="https://test.tradinglab.app/renew",
        hotmart_hottok=None,
    )
    monkeypatch.setattr("apps.license_server.settings.get_settings", lambda: settings)
    return settings


@pytest.fixture
def app_client(test_settings: Settings) -> Iterator[TestClient]:
    """TestClient without requiring external PostgreSQL."""
    app = create_app(test_settings)
    load_signing_key(test_settings)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def pg_app(test_settings: Settings) -> Iterator[TestClient]:
    """TestClient running against a real PostgreSQL database.

    Skipped if TEST_DATABASE_URL is not set.
    """
    db_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not db_url:
        pytest.skip("TEST_DATABASE_URL is not configured; skipping Postgres-dependent test")

    pool = init_pool(db_url)
    with pool.connection() as conn:
        run_migrations(conn)
        init_keys_in_db(conn, test_settings)

    app = create_app(test_settings)
    with TestClient(app) as client:
        yield client

    close_pool()
