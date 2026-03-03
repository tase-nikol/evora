from __future__ import annotations

from typing import Awaitable, Callable

import anyio

from .base import Message


class MemoryBroker:
    """
    An in-memory message broker using anyio memory object streams.
    Provides publish/subscribe functionality for channels.
    """

    def __init__(self) -> None:
        """
        Initialize the MemoryBroker with empty channel queues and receivers.
        """
        self._queues: dict[str, anyio.abc.ObjectSendStream[Message]] = {}
        self._recv: dict[str, anyio.abc.ObjectReceiveStream[Message]] = {}

    def _ensure_channel(self, channel: str) -> None:
        """
        Ensure that a memory stream exists for the given channel.
        If not, create a new send/receive stream pair for the channel.

        Args:
            channel: The name of the channel.
        """
        if channel in self._queues:
            return
        send, recv = anyio.create_memory_object_stream[Message](max_buffer_size=10_000)
        self._queues[channel] = send
        self._recv[channel] = recv

    async def publish(
        self,
        channel: str,
        *,
        value: bytes,
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """
        Publish a message to a channel.

        Args:
            channel: The channel to publish to.
            value: The message payload as bytes.
            key: Optional message key.
            headers: Optional message headers.
        """
        self._ensure_channel(channel)
        msg = Message(channel=channel, value=value, headers=headers or {}, key=key)
        await self._queues[channel].send(msg)

    async def run_consumer(
        self,
        channels: list[str],
        handler: Callable[[Message], Awaitable[None]],
        *,
        consumer_name: str,
    ) -> None:
        """
        Run a consumer that listens to the given channels and processes messages with the handler.

        Args:
            channels: List of channel names to consume from.
            handler: Async callable to process each message.
            consumer_name: Name of the consumer (for identification/logging).
        """
        for ch in channels:
            self._ensure_channel(ch)

        async def loop(ch: str) -> None:
            recv = self._recv[ch]
            async for msg in recv:
                await handler(msg)

        async with anyio.create_task_group() as tg:
            for ch in channels:
                tg.start_soon(loop, ch)
