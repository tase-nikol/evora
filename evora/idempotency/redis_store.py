from __future__ import annotations

from dataclasses import dataclass

import redis.asyncio as redis

from .base import IdempotencyStore


@dataclass
class RedisIdempotencyStore(IdempotencyStore):
    client: redis.Redis
    prefix: str = "evora:idem"

    def _key(self, scope: str, event_id: str) -> str:
        return f"{self.prefix}:{scope}:{event_id}"

    async def seen(self, *, scope: str, event_id: str) -> bool:
        result = await self.client.exists(self._key(scope, event_id))
        return bool(result)

    async def mark_seen(self, *, scope: str, event_id: str, ttl_seconds: int) -> None:
        # SET key "1" NX EX ttl
        await self.client.set(self._key(scope, event_id), "1", nx=True, ex=ttl_seconds)
