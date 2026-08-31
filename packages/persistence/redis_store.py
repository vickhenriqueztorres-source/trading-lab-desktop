"""Optional async Redis primitives for leases and ephemeral worker state."""

from __future__ import annotations

import json
import time
from typing import Any


class RedisStore:
    LEASE_PREFIX = "worker_leases:"
    SIGNAL_PREFIX = "signals:"
    EPHEMERAL_PREFIX = "ephemeral_state:"
    _fallback_counters: dict[str, int] = {}
    _fallback_data: dict[str, tuple[str, float]] = {}

    def __init__(self, url: str | None = None, *, client: Any | None = None) -> None:
        self.url = url
        self.client = client

    async def connect(self) -> None:
        if self.client is not None:
            return
        if not self.url:
            raise RuntimeError("REDIS_URL_REQUIRED")
        try:
            from redis import asyncio as redis_asyncio  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("REDIS_DRIVER_NOT_INSTALLED") from exc
        self.client = redis_asyncio.from_url(self.url, decode_responses=True)

    async def close(self) -> None:
        if self.client is not None and hasattr(self.client, "aclose"):
            await self.client.aclose()
        self.client = None

    async def acquire_lease(
        self, resource: str, owner: str, ttl_seconds: int
    ) -> tuple[int, float] | None:
        key = self.LEASE_PREFIX + resource
        counter_key = key + ":fencing"
        if self.client is None:
            now = time.monotonic()
            current = self._fallback_data.get(key)
            if current is not None and current[1] > now:
                return None
            token = self._fallback_counters.get(counter_key, 0) + 1
            self._fallback_counters[counter_key] = token
        else:
            claimed = await self.client.set(key, "__lease_claim__", nx=True, ex=ttl_seconds)
            if not claimed:
                return None
            token = int(await self.client.incr(counter_key))
        value = json.dumps({"owner": owner, "fencing_token": token})
        if self.client is None:
            self._fallback_data[key] = (value, now + ttl_seconds)
            return token, now + ttl_seconds
        await self.client.set(key, value, ex=ttl_seconds)
        return token, time.monotonic() + ttl_seconds

    async def renew_lease(
        self, resource: str, owner: str, fencing_token: int, ttl_seconds: int
    ) -> bool:
        key = self.LEASE_PREFIX + resource
        expected = json.dumps({"owner": owner, "fencing_token": fencing_token})
        if self.client is None:
            current = self._fallback_data.get(key)
            if current is None or current[1] <= time.monotonic() or current[0] != expected:
                return False
            self._fallback_data[key] = (expected, time.monotonic() + ttl_seconds)
            return True
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
        )
        return bool(await self.client.eval(script, 1, key, expected, ttl_seconds))

    async def release_lease(self, resource: str, owner: str, fencing_token: int) -> bool:
        key = self.LEASE_PREFIX + resource
        expected = json.dumps({"owner": owner, "fencing_token": fencing_token})
        if self.client is None:
            current = self._fallback_data.get(key)
            if current is not None and current[0] == expected:
                self._fallback_data.pop(key, None)
                return True
            return False
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end"
        )
        return bool(await self.client.eval(script, 1, key, expected))

    async def lease_owner(self, resource: str) -> dict[str, Any] | None:
        key = self.LEASE_PREFIX + resource
        raw = (
            self._fallback_data.get(key, (None, 0))[0]
            if self.client is None
            else await self.client.get(key)
        )
        if not raw:
            return None
        return dict(json.loads(raw))

    async def set_signal(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        await self._set(self.SIGNAL_PREFIX + key, value, ttl_seconds)

    async def get_signal(self, key: str) -> Any:
        return await self._get(self.SIGNAL_PREFIX + key)

    async def set_ephemeral(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        await self._set(self.EPHEMERAL_PREFIX + key, value, ttl_seconds)

    async def get_ephemeral(self, key: str) -> Any:
        return await self._get(self.EPHEMERAL_PREFIX + key)

    async def _set(self, key: str, value: Any, ttl_seconds: int | None) -> None:
        encoded = json.dumps(value, default=str)
        if self.client is None:
            self._fallback_data[key] = (encoded, time.monotonic() + (ttl_seconds or 86_400))
        elif ttl_seconds is None:
            await self.client.set(key, encoded)
        else:
            await self.client.set(key, encoded, ex=ttl_seconds)

    async def _get(self, key: str) -> Any:
        raw = (
            self._fallback_data.get(key, (None, 0))[0]
            if self.client is None
            else await self.client.get(key)
        )
        return None if raw is None else json.loads(raw)


__all__ = ["RedisStore"]
