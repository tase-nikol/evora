"""Tests for the MemoryBroker."""

import asyncio

import pytest

from evora.brokers.base import Message
from evora.brokers.memory import MemoryBroker


@pytest.mark.asyncio
async def test_memory_broker_publish_and_consume():
    """Test publishing and consuming messages with MemoryBroker."""
    broker = MemoryBroker()
    received_messages = []

    async def handler(msg: Message):
        received_messages.append(msg)

    # Start consumer in background
    consumer_task = asyncio.create_task(
        broker.run_consumer(
            channels=["test-channel"],
            handler=handler,
            consumer_name="test-consumer",
        )
    )

    # Give consumer time to start
    await asyncio.sleep(0.1)

    # Publish messages
    await broker.publish(
        channel="test-channel",
        value=b"test message 1",
        key="key1",
        headers={"header1": "value1"},
    )

    await broker.publish(
        channel="test-channel",
        value=b"test message 2",
        key="key2",
        headers={"header2": "value2"},
    )

    # Give consumer time to process
    await asyncio.sleep(0.1)

    # Cancel consumer
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    # Verify messages were received
    assert len(received_messages) == 2
    assert received_messages[0].value == b"test message 1"
    assert received_messages[0].key == "key1"
    assert received_messages[0].headers == {"header1": "value1"}
    assert received_messages[1].value == b"test message 2"
    assert received_messages[1].key == "key2"
    assert received_messages[1].headers == {"header2": "value2"}


@pytest.mark.asyncio
async def test_memory_broker_multiple_channels():
    """Test consuming from multiple channels."""
    broker = MemoryBroker()
    received_messages = []

    async def handler(msg: Message):
        received_messages.append(msg)

    # Start consumer for multiple channels
    consumer_task = asyncio.create_task(
        broker.run_consumer(
            channels=["channel-1", "channel-2"],
            handler=handler,
            consumer_name="multi-channel-consumer",
        )
    )

    await asyncio.sleep(0.1)

    # Publish to different channels
    await broker.publish(channel="channel-1", value=b"message to channel 1")
    await broker.publish(channel="channel-2", value=b"message to channel 2")

    await asyncio.sleep(0.1)

    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    assert len(received_messages) == 2
    channels = {msg.channel for msg in received_messages}
    assert channels == {"channel-1", "channel-2"}


@pytest.mark.asyncio
async def test_memory_broker_no_key_or_headers():
    """Test publishing without optional key and headers."""
    broker = MemoryBroker()
    received_messages = []

    async def handler(msg: Message):
        received_messages.append(msg)

    consumer_task = asyncio.create_task(
        broker.run_consumer(
            channels=["simple-channel"],
            handler=handler,
            consumer_name="simple-consumer",
        )
    )

    await asyncio.sleep(0.1)

    # Publish without key and headers
    await broker.publish(channel="simple-channel", value=b"simple message")

    await asyncio.sleep(0.1)

    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    assert len(received_messages) == 1
    assert received_messages[0].value == b"simple message"
    assert received_messages[0].key is None
    assert received_messages[0].headers == {}
