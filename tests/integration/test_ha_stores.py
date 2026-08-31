from __future__ import annotations

import asyncio

from packages.persistence.postgres_store import PostgresStore
from packages.persistence.redis_store import RedisStore


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: str) -> str:
        self.statements.append(statement)
        return "OK"


class FakePool:
    def __init__(self) -> None:
        self.connection = FakeConnection()

    async def acquire(self) -> FakeConnection:
        return self.connection

    async def release(self, connection: FakeConnection) -> None:
        pass


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    async def set(self, key: str, value: str, **kwargs: object) -> bool:
        if kwargs.get("nx") and key in self.data:
            return False
        self.data[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def eval(self, script: str, count: int, key: str, expected: str, *args: object) -> int:
        if self.data.get(key) != expected:
            return 0
        if "del" in script:
            self.data.pop(key, None)
            return 1
        return 1


def test_postgres_store_runs_versioned_migration() -> None:
    async def scenario() -> None:
        pool = FakePool()
        store = PostgresStore(pool=pool)
        await store.connect()
        await store.migrate()
        assert "CREATE TABLE IF NOT EXISTS orders" in pool.connection.statements[0]

    asyncio.run(scenario())


def test_redis_store_lease_is_atomic_and_state_is_ephemeral() -> None:
    async def scenario() -> None:
        store = RedisStore(client=FakeRedis())
        first = await store.acquire_lease("resource", "a", 45)
        second = await store.acquire_lease("resource", "b", 45)
        assert first is not None
        assert second is None
        await store.set_signal("s", {"value": 1})
        assert await store.get_signal("s") == {"value": 1}
        assert await store.release_lease("resource", "a", first[0])

    asyncio.run(scenario())
