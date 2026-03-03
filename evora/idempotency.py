from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

IdempotencyMode = Literal["event_id"]  # we can add "key" / "custom" later


class IdempotencyStore(Protocol):
    """
    Protocol for an idempotency store that tracks event processing.

    Methods:
        seen: Check if an event has already been processed within a given scope.
        mark_seen: Mark an event as processed with a specified TTL.
    """

    async def seen(self, *, scope: str, event_id: str) -> bool: ...
    async def mark_seen(self, *, scope: str, event_id: str, ttl_seconds: int) -> None: ...


@dataclass(frozen=True)
class IdempotencyPolicy:
    """
    Configuration for idempotency behavior.

    Attributes:
        mode: The idempotency mode (default: "event_id").
        ttl_seconds: Time-to-live for idempotency records in seconds (default: 86,400).
    """

    mode: IdempotencyMode = "event_id"
    ttl_seconds: int = 86_400
