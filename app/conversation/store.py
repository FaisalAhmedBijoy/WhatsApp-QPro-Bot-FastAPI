"""Tiny key/value store with TTL, used for conversation sessions, message de-duplication and
"last used branch". In-memory for local demo; Redis for anything with more than one worker."""

import json
import time
from typing import Protocol

from redis.asyncio import Redis


class KeyValueStore(Protocol):
    async def get(self, key: str) -> dict | None: ...
    async def set(self, key: str, value: dict, ttl_seconds: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def add_if_absent(self, key: str, ttl_seconds: int) -> bool:
        """Atomically mark ``key``; returns False if it already existed (used for de-dup)."""
        ...
    async def close(self) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, dict]] = {}

    def _live(self, key: str) -> dict | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            del self._data[key]
            return None
        return value

    async def get(self, key: str) -> dict | None:
        value = self._live(key)
        return json.loads(json.dumps(value)) if value is not None else None

    async def set(self, key: str, value: dict, ttl_seconds: int) -> None:
        self._data[key] = (time.monotonic() + ttl_seconds, json.loads(json.dumps(value)))

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def add_if_absent(self, key: str, ttl_seconds: int) -> bool:
        if self._live(key) is not None:
            return False
        self._data[key] = (time.monotonic() + ttl_seconds, {})
        return True

    async def close(self) -> None:
        self._data.clear()


class RedisStore:
    def __init__(self, url: str, prefix: str = "qpro-bot:") -> None:
        self._redis = Redis.from_url(url, decode_responses=True)
        self._prefix = prefix

    async def get(self, key: str) -> dict | None:
        raw = await self._redis.get(self._prefix + key)
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: dict, ttl_seconds: int) -> None:
        await self._redis.set(self._prefix + key, json.dumps(value), ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._redis.delete(self._prefix + key)

    async def add_if_absent(self, key: str, ttl_seconds: int) -> bool:
        return bool(await self._redis.set(self._prefix + key, "1", ex=ttl_seconds, nx=True))

    async def close(self) -> None:
        await self._redis.aclose()
