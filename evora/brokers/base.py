from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional, Protocol


@dataclass
class Message:
    channel: str
    value: bytes
    headers: Dict[str, str]
    key: Optional[str] = None

    # Broker metadata (optional)
    message_id: Optional[str] = None
    attempt: int = 1


class BaseBroker(Protocol):
    async def publish(
        self, channel: str, *, value: bytes, key: str | None = None, headers: dict[str, str] | None = None
    ) -> None: ...

    async def run_consumer(
        self,
        channels: list[str],
        handler: Callable[[Message], Awaitable[None]],
        *,
        consumer_name: str,
    ) -> None: ...

    async def schedule_retry(
            self,
            *,
            msg: Message,
            raw_value: bytes,
            headers: dict[str, str],
            attempt: int,
            error_type: str,
            error_message: str,
    ) -> None: ...