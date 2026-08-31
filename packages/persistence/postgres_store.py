"""Optional async PostgreSQL store for HA deployments.

The desktop remains SQLite-first.  This module keeps PostgreSQL optional and
accepts an injected async connection/pool for deterministic tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

SCHEMA_VERSION = 1


class PostgresStore:
    def __init__(self, dsn: str | None = None, *, pool: Any | None = None) -> None:
        self.dsn = dsn
        self.pool = pool

    async def connect(self) -> None:
        if self.pool is not None:
            return
        if not self.dsn:
            raise RuntimeError("POSTGRES_DSN_REQUIRED")
        try:
            import asyncpg  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("POSTGRES_DRIVER_NOT_INSTALLED") from exc
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)

    async def close(self) -> None:
        if self.pool is not None and hasattr(self.pool, "close"):
            await self.pool.close()
        self.pool = None

    async def migrate(self) -> None:
        async with self.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    name TEXT PRIMARY KEY, version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY, broker TEXT NOT NULL,
                    environment TEXT NOT NULL, currency TEXT, created_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    internal_order_id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE,
                    account_id TEXT NOT NULL, strategy_id TEXT NOT NULL, asset TEXT NOT NULL,
                    direction TEXT NOT NULL, amount NUMERIC NOT NULL, duration INTEGER NOT NULL,
                    state TEXT NOT NULL, timestamps_json JSONB NOT NULL,
                    fencing_token TEXT NOT NULL, reconciliation_id TEXT
                );
                CREATE TABLE IF NOT EXISTS order_events (
                    event_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                    event_type TEXT NOT NULL, timestamp TIMESTAMPTZ NOT NULL,
                    payload_json JSONB NOT NULL, correlation_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_reservations (
                    reservation_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                    amount NUMERIC NOT NULL, currency TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, released_at TIMESTAMPTZ
                );
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    dedupe_key TEXT PRIMARY KEY, intent_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL, payload_json JSONB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deployment_events (
                    event_id TEXT PRIMARY KEY, version TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL, payload_json JSONB NOT NULL
                );
                INSERT INTO schema_versions(name, version) VALUES ('enterprise_foundation', 1)
                    ON CONFLICT(name) DO NOTHING;
                """
            )

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        if self.pool is None:
            raise RuntimeError("POSTGRES_NOT_CONNECTED")
        acquire = getattr(self.pool, "acquire", None)
        if acquire is None:
            yield self.pool
            return
        connection = await acquire()
        try:
            yield connection
        finally:
            release = getattr(self.pool, "release", None)
            if release is not None:
                result = release(connection)
                if hasattr(result, "__await__"):
                    await result

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        async with self.acquire() as connection:
            transaction = connection.transaction()
            await transaction.start()
            try:
                yield connection
            except BaseException:
                await transaction.rollback()
                raise
            else:
                await transaction.commit()


__all__ = ["PostgresStore", "SCHEMA_VERSION"]
