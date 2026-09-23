"""PostgreSQL connection pool and idempotent migrations for Trading Lab License Server."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool

from apps.license_server.errors import ApiError, ErrorCode

logger = logging.getLogger("license_server.db")

_POOL: ConnectionPool | None = None

MIGRATION_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS customers (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email text NOT NULL UNIQUE,
        name text,
        country text,
        notes text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS licenses (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        plan text NOT NULL DEFAULT 'PRO',
        status text NOT NULL DEFAULT 'active',
        broker_access text[] NOT NULL DEFAULT ARRAY['DERIV','IQ_OPTION'],
        strategy_packs text[] NOT NULL DEFAULT ARRAY['core'],
        real_mode_allowed boolean NOT NULL DEFAULT false,
        max_devices int NOT NULL DEFAULT 1,
        starts_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    """,
    "CREATE INDEX IF NOT EXISTS licenses_customer_idx ON licenses(customer_id);",
    """
    CREATE TABLE IF NOT EXISTS devices (
        device_id text PRIMARY KEY,
        customer_id uuid NOT NULL,
        public_key_b64 text NOT NULL,
        label text,
        revoked boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        last_seen_at timestamptz NOT NULL DEFAULT now()
    );
    """,
    "CREATE INDEX IF NOT EXISTS devices_customer_idx ON devices(customer_id);",
    """
    CREATE TABLE IF NOT EXISTS otp_challenges (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email text NOT NULL,
        pkce_challenge text NOT NULL,
        code_digest text NOT NULL,
        code_plain text,
        delivery_status text NOT NULL DEFAULT 'pending',
        attempts int NOT NULL DEFAULT 0,
        expires_at timestamptz NOT NULL,
        consumed boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    """,
    "CREATE INDEX IF NOT EXISTS otp_email_idx ON otp_challenges(email, created_at DESC);",
    """
    CREATE TABLE IF NOT EXISTS api_tokens (
        token_digest text PRIMARY KEY,
        kind text NOT NULL,
        customer_id uuid NOT NULL,
        family_id uuid NOT NULL,
        expires_at timestamptz NOT NULL,
        used boolean NOT NULL DEFAULT false,
        revoked boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    """,
    "CREATE INDEX IF NOT EXISTS api_tokens_family_idx ON api_tokens(family_id);",
    """
    CREATE TABLE IF NOT EXISTS device_challenges (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        customer_id uuid NOT NULL,
        device_id text NOT NULL,
        nonce_b64 text NOT NULL,
        expires_at timestamptz NOT NULL,
        consumed boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS leases (
        lease_id uuid PRIMARY KEY,
        customer_id uuid NOT NULL,
        device_id text NOT NULL,
        issued_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        revoked boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS signing_keys (
        key_id text PRIMARY KEY,
        private_key_pem text NOT NULL,
        public_key_b64 text NOT NULL,
        active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id bigserial PRIMARY KEY,
        customer_id uuid,
        actor text NOT NULL,
        action text NOT NULL,
        details jsonb,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    """,
)


def init_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    """Initialize the global connection pool."""
    global _POOL
    if not database_url.strip():
        raise ValueError("DATABASE_URL cannot be empty")
    if _POOL is not None:
        _POOL.close()
    _POOL = ConnectionPool(
        conninfo=database_url,
        min_size=min_size,
        max_size=max_size,
        open=True,
    )
    return _POOL


def close_pool() -> None:
    """Close the global connection pool."""
    global _POOL
    if _POOL is not None:
        _POOL.close()
        _POOL = None


def get_pool() -> ConnectionPool | None:
    """Return current connection pool if open."""
    return _POOL


_DEV_DB: Any = None


def get_dev_db() -> Any:
    """Return in-memory dev database for local testing without external Postgres."""
    global _DEV_DB
    if _DEV_DB is None:
        from datetime import UTC, datetime, timedelta
        from uuid import uuid4

        from apps.license_server.fake_db import FakeDb

        _DEV_DB = FakeDb()
        cid = uuid4()
        _DEV_DB.customers[str(cid)] = {
            "id": str(cid),
            "email": "usuario@tradinglab.app",
            "name": "Trader Local",
            "country": "BR",
            "notes": "Usuario local preconfigurado para testes",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        _DEV_DB.add_active_license(
            cid,
            plan="PRO",
            expires_at=datetime.now(UTC) + timedelta(days=365),
            real_mode_allowed=True,
            broker_access=["DERIV", "IQ_OPTION"],
            strategy_packs=["core", "deriv-digits", "iqoption-rsi"],
            max_devices=2,
        )

        cid_admin = uuid4()
        _DEV_DB.customers[str(cid_admin)] = {
            "id": str(cid_admin),
            "email": "admin@tradinglab.app",
            "name": "Administrador Trading Lab",
            "country": "BR",
            "notes": "Administrador com licenca PRO ativa",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        _DEV_DB.add_active_license(
            cid_admin,
            plan="PRO",
            expires_at=datetime.now(UTC) + timedelta(days=365),
            real_mode_allowed=True,
            broker_access=["DERIV", "IQ_OPTION"],
            strategy_packs=["core", "deriv-digits", "iqoption-rsi"],
            max_devices=2,
        )
    return _DEV_DB


@contextmanager
def with_conn() -> Iterator[Any]:
    """Context manager checking out a connection from the pool or dev store."""
    if _POOL is not None:
        try:
            with _POOL.connection() as conn:
                yield conn
        except ApiError:
            raise
        except Exception as exc:
            logger.error("Database connection error: %s", exc)
            raise ApiError(ErrorCode.AUTH_SERVICE_UNAVAILABLE, "Database connection error") from exc
    else:
        from apps.license_server.settings import get_settings

        settings = get_settings()
        if settings.environment != "development":
            raise ApiError(ErrorCode.AUTH_SERVICE_UNAVAILABLE, "Database service is not connected")
        from apps.license_server.fake_db import FakeConnection

        yield FakeConnection(get_dev_db())


def run_migrations(conn: Connection[Any]) -> None:
    """Run all schema migrations idempotently (one statement per execute)."""
    with conn.cursor() as cur:
        for statement in MIGRATION_STATEMENTS:
            cleaned = statement.strip()
            if cleaned:
                cur.execute(cleaned)
    conn.commit()
    logger.info("Database migrations applied successfully")
