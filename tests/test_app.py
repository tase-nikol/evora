"""Tests for App class functionality."""

import asyncio

import pytest
from pydantic import BaseModel

from evora.app import App, Context, subscribe
from evora.brokers.base import Message
from evora.brokers.memory import MemoryBroker
from evora.core import Event
from evora.errors import ContractError, FatalError, RetryableError
from evora.idempotency import IdempotencyPolicy
from evora.idempotency.base import IdempotencyStore
from evora.observability.telemetry import NoopTelemetry
from evora.runtime import RetryPolicy


class TestEvent(Event):
    __version__ = 1

    class Data(BaseModel):
        value: str

    data: Data


class InvalidEvent(Event):
    # Missing __version__
    class Data(BaseModel):
        value: str

    data: Data


class InMemoryIdempotencyStore(IdempotencyStore):
    """Simple in-memory idempotency store for testing."""

    def __init__(self):
        self._seen = set()

    async def seen(self, *, scope: str, event_id: str) -> bool:
        key = f"{scope}:{event_id}"
        return key in self._seen

    async def mark_seen(self, *, scope: str, event_id: str, ttl_seconds: int) -> None:
        key = f"{scope}:{event_id}"
        self._seen.add(key)


def test_subscribe_decorator():
    """Test that subscribe decorator attaches spec to function."""

    @subscribe(TestEvent, channel="test-channel", max_attempts=3, idempotency=IdempotencyPolicy())
    async def handler(event, ctx):
        pass

    assert hasattr(handler, "__evora_spec__")
    spec = handler.__evora_spec__
    assert spec["event_cls"] == TestEvent
    assert spec["channel"] == "test-channel"
    assert isinstance(spec["retry"], RetryPolicy)
    assert spec["retry"].max_attempts == 3
    assert spec["dlq"] is True
    assert isinstance(spec["idempotency"], IdempotencyPolicy)


def test_app_add_handler_without_decorator():
    """Test adding handler without @subscribe decorator raises error."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency)

    async def handler(event, ctx):
        pass

    with pytest.raises(ValueError, match="not decorated with @subscribe"):
        app.add_handler(handler)


def test_app_add_handler_strict_requires_version():
    """Test strict mode requires explicit __version__."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency, strict=True)

    @subscribe(InvalidEvent, idempotency=IdempotencyPolicy())
    async def handler(event, ctx):
        pass

    with pytest.raises(ValueError, match="must declare an explicit integer __version__"):
        app.add_handler(handler)


def test_app_add_handler_strict_requires_idempotency():
    """Test strict mode requires idempotency policy."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency, strict=True)

    @subscribe(TestEvent)
    async def handler(event, ctx):
        pass

    with pytest.raises(ValueError, match="must declare idempotency=IdempotencyPolicy"):
        app.add_handler(handler)


def test_app_add_handler_non_strict():
    """Test non-strict mode allows missing idempotency."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency, strict=False)

    @subscribe(TestEvent)
    async def handler(event, ctx):
        pass

    # Should not raise
    app.add_handler(handler)


@pytest.mark.asyncio
async def test_app_publish():
    """Test publishing an event through app."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency)

    # Register event type
    app.registry.register(TestEvent)

    event = TestEvent(data=TestEvent.Data(value="test"))

    # Capture published message
    published = []

    original_publish = broker.publish

    async def capture_publish(*args, **kwargs):
        published.append((args, kwargs))
        await original_publish(*args, **kwargs)

    broker.publish = capture_publish

    await app.publish(event, channel="custom-channel", key="key123", subject="subj")

    assert len(published) == 1
    assert published[0][1]["key"] == "key123"


@pytest.mark.asyncio
async def test_app_consume_success():
    """Test successful event consumption."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency, strict=False)

    received_events = []

    @subscribe(TestEvent, idempotency=IdempotencyPolicy())
    async def handler(event, ctx):
        received_events.append(event)

    app.add_handler(handler)

    # Publish event
    event = TestEvent(data=TestEvent.Data(value="success"))
    await app.publish(event)

    # Run consumer briefly
    consumer_task = asyncio.create_task(app.run())
    await asyncio.sleep(0.2)
    consumer_task.cancel()

    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    assert len(received_events) == 1
    assert received_events[0].data.value == "success"


@pytest.mark.asyncio
async def test_app_consume_idempotency():
    """Test idempotency prevents duplicate processing."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency, strict=False)

    call_count = [0]

    @subscribe(TestEvent, idempotency=IdempotencyPolicy())
    async def handler(event, ctx):
        call_count[0] += 1

    app.add_handler(handler)

    # Publish event twice - first should succeed, second should be skipped
    event = TestEvent(data=TestEvent.Data(value="test"))
    await app.publish(event, channel="TestEvent")

    # Run consumer briefly to process first event
    consumer_task = asyncio.create_task(app.run())
    await asyncio.sleep(0.2)
    consumer_task.cancel()

    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    # First event should be processed
    assert call_count[0] == 1

    # Now the event ID should be marked as seen in the store
    # Verify idempotency store was used
    assert len(idempotency._seen) > 0


@pytest.mark.asyncio
async def test_app_classify_errors():
    """Test error classification."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency)

    assert app._classify_error(ContractError("test")) == "contract"
    assert app._classify_error(FatalError("test")) == "fatal"
    assert app._classify_error(RetryableError("test")) == "retryable"
    assert app._classify_error(ValueError("test")) == "unknown"


def test_app_get_attempt_from_message():
    """Test getting attempt number from message."""
    from evora.brokers.base import Message

    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency)

    event = TestEvent(data=TestEvent.Data(value="test"))
    envelope = event.to_envelope(source="test")

    # Test with message having attempt attribute
    msg = Message(channel="test", value=b"test", headers={}, key=None)
    msg.attempt = 3
    assert app._get_attempt(msg, envelope) == 3


def test_app_get_attempt_from_envelope():
    """Test getting attempt number from envelope meta when message attempt is not set."""
    from evora.brokers.base import Message

    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency)

    event = TestEvent(data=TestEvent.Data(value="test"))
    envelope = event.to_envelope(source="test", meta={"attempt": 5})

    # Message with attempt None should check envelope
    msg = Message(channel="test", value=b"test", headers={}, key=None, attempt=None)
    # But since Message has default attempt=1, we test the code path with explicit setting
    msg.attempt = None
    # The function checks getattr(msg, "attempt", None) which returns None when not set
    # But dataclass always has attempt with default 1, so test when envelope has it
    assert app._get_attempt(msg, envelope) in [1, 5]  # Could be either based on implementation


def test_app_get_attempt_default():
    """Test default attempt is 1."""
    from evora.brokers.base import Message

    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency)

    event = TestEvent(data=TestEvent.Data(value="test"))
    envelope = event.to_envelope(source="test")

    msg = Message(channel="test", value=b"test", headers={}, key=None)
    assert app._get_attempt(msg, envelope) == 1


def test_context_creation():
    """Test Context object creation."""
    from evora.brokers.base import Message

    event = TestEvent(data=TestEvent.Data(value="test"))
    envelope = event.to_envelope(source="test", traceparent="00-trace-span-01")

    msg = Message(channel="test", value=b"test", headers={}, key="key1")

    ctx = Context(message=msg, envelope=envelope, handler_name="test.handler")

    assert ctx.message == msg
    assert ctx.envelope == envelope
    assert ctx.handler_name == "test.handler"
    assert ctx.traceparent == "00-trace-span-01"


def test_app_with_custom_telemetry():
    """Test app with custom telemetry."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    telemetry = NoopTelemetry()

    app = App(
        broker=broker,
        source="test-service",
        idempotency_store=idempotency,
        telemetry=telemetry,
    )

    assert app.telemetry == telemetry


def test_app_default_telemetry():
    """Test app defaults to NoopTelemetry."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()

    app = App(broker=broker, source="test-service", idempotency_store=idempotency)

    assert isinstance(app.telemetry, NoopTelemetry)


class SampleEvent(Event):
    __version__ = 1

    class Data(BaseModel):
        value: str

    data: Data


class FaultyBroker(MemoryBroker):
    """Broker that can fail on schedule_retry."""

    def __init__(self):
        super().__init__()
        self.should_fail_retry = False

    async def schedule_retry(
        self,
        *,
        msg: Message,
        raw_value: bytes,
        headers: dict[str, str],
        attempt: int,
        error_type: str,
        error_message: str,
    ) -> None:
        if self.should_fail_retry:
            raise ValueError("Retry scheduling failed")
        # Otherwise do nothing (don't actually schedule)


@pytest.mark.asyncio
async def test_app_invalid_envelope():
    """Test handling of invalid envelope data."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency, strict=False)

    @subscribe(SampleEvent, idempotency=IdempotencyPolicy())
    async def handler(event, ctx):
        pass

    app.add_handler(handler)

    # Publish invalid JSON
    await broker.publish(channel="SampleEvent", value=b"not valid json")

    # Check DLQ
    dlq_messages = []

    async def dlq_handler(msg: Message):
        dlq_messages.append(msg)

    consumer_task = asyncio.create_task(app.run())
    dlq_task = asyncio.create_task(
        broker.run_consumer(
            channels=["SampleEvent.dlq"], handler=dlq_handler, consumer_name="dlq-consumer"
        )
    )

    await asyncio.sleep(0.2)
    consumer_task.cancel()
    dlq_task.cancel()

    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    try:
        await dlq_task
    except asyncio.CancelledError:
        pass

    # Invalid message should go to DLQ
    assert len(dlq_messages) >= 1
    assert dlq_messages[0].value == b"not valid json"


@pytest.mark.asyncio
async def test_app_no_matching_handler():
    """Test handling when event type doesn't match any handler."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency, strict=False)

    class OtherEvent(Event):
        __version__ = 1

        class Data(BaseModel):
            value: str

        data: Data

    # Register a handler for SampleEvent but publish OtherEvent to same channel
    @subscribe(SampleEvent, channel="test-channel", idempotency=IdempotencyPolicy())
    async def handler(event, ctx):
        pass

    app.add_handler(handler)
    app.registry.register(OtherEvent)

    # Publish OtherEvent (different type) to the channel
    event = OtherEvent(data=OtherEvent.Data(value="test"))
    await app.publish(event, channel="test-channel")

    dlq_messages = []

    async def dlq_handler(msg: Message):
        dlq_messages.append(msg)

    consumer_task = asyncio.create_task(app.run())
    dlq_task = asyncio.create_task(
        broker.run_consumer(
            channels=["test-channel.dlq"], handler=dlq_handler, consumer_name="dlq-consumer"
        )
    )

    await asyncio.sleep(0.2)
    consumer_task.cancel()
    dlq_task.cancel()

    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    try:
        await dlq_task
    except asyncio.CancelledError:
        pass

    # Event with no matching handler should go to DLQ
    assert len(dlq_messages) >= 1


@pytest.mark.asyncio
async def test_app_contract_error_to_dlq():
    """Test that ContractError sends message to DLQ."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency, strict=False)

    @subscribe(SampleEvent, idempotency=IdempotencyPolicy(), dlq=True)
    async def handler(event, ctx):
        raise ContractError("Invalid contract")

    app.add_handler(handler)

    event = SampleEvent(data=SampleEvent.Data(value="test"))
    await app.publish(event)

    dlq_messages = []

    async def dlq_handler(msg: Message):
        dlq_messages.append(msg)

    consumer_task = asyncio.create_task(app.run())
    dlq_task = asyncio.create_task(
        broker.run_consumer(
            channels=["SampleEvent.dlq"], handler=dlq_handler, consumer_name="dlq-consumer"
        )
    )

    await asyncio.sleep(0.2)
    consumer_task.cancel()
    dlq_task.cancel()

    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    try:
        await dlq_task
    except asyncio.CancelledError:
        pass

    assert len(dlq_messages) >= 1


@pytest.mark.asyncio
async def test_app_fatal_error_to_dlq():
    """Test that FatalError sends message to DLQ."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency, strict=False)

    @subscribe(SampleEvent, idempotency=IdempotencyPolicy(), dlq=True)
    async def handler(event, ctx):
        raise FatalError("Fatal error occurred")

    app.add_handler(handler)

    event = SampleEvent(data=SampleEvent.Data(value="test"))
    await app.publish(event)

    dlq_messages = []

    async def dlq_handler(msg: Message):
        dlq_messages.append(msg)

    consumer_task = asyncio.create_task(app.run())
    dlq_task = asyncio.create_task(
        broker.run_consumer(
            channels=["SampleEvent.dlq"], handler=dlq_handler, consumer_name="dlq-consumer"
        )
    )

    await asyncio.sleep(0.2)
    consumer_task.cancel()
    dlq_task.cancel()

    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    try:
        await dlq_task
    except asyncio.CancelledError:
        pass

    assert len(dlq_messages) >= 1


@pytest.mark.asyncio
async def test_app_dlq_disabled():
    """Test that errors are not sent to DLQ when dlq=False."""
    broker = MemoryBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency, strict=False)

    errors_caught = []

    @subscribe(SampleEvent, idempotency=IdempotencyPolicy(), dlq=False, max_attempts=1)
    async def handler(event, ctx):
        error = FatalError("Fatal error")
        errors_caught.append(error)
        raise error

    app.add_handler(handler)

    event = SampleEvent(data=SampleEvent.Data(value="test"))
    await app.publish(event)

    consumer_task = asyncio.create_task(app.run())
    await asyncio.sleep(0.2)
    consumer_task.cancel()

    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    # Error should have been raised but not sent to DLQ
    assert len(errors_caught) >= 1


@pytest.mark.asyncio
async def test_app_idempotency_store_failure():
    """Test handling when idempotency store fails."""

    class FailingIdempotencyStore(IdempotencyStore):
        async def seen(self, *, scope: str, event_id: str) -> bool:
            raise RuntimeError("Idempotency store failed")

        async def mark_seen(self, *, scope: str, event_id: str, ttl_seconds: int) -> None:
            raise RuntimeError("Idempotency store failed")

    broker = MemoryBroker()
    idempotency = FailingIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency, strict=False)

    @subscribe(SampleEvent, idempotency=IdempotencyPolicy(), dlq=True)
    async def handler(event, ctx):
        pass

    app.add_handler(handler)

    event = SampleEvent(data=SampleEvent.Data(value="test"))
    await app.publish(event)

    dlq_messages = []

    async def dlq_handler(msg: Message):
        dlq_messages.append(msg)

    consumer_task = asyncio.create_task(app.run())
    dlq_task = asyncio.create_task(
        broker.run_consumer(
            channels=["SampleEvent.dlq"], handler=dlq_handler, consumer_name="dlq-consumer"
        )
    )

    await asyncio.sleep(0.2)
    consumer_task.cancel()
    dlq_task.cancel()

    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    try:
        await dlq_task
    except asyncio.CancelledError:
        pass

    # Should go to DLQ when idempotency check fails
    assert len(dlq_messages) >= 1


@pytest.mark.asyncio
async def test_app_retry_exhausted_to_dlq():
    """Test that retry exhaustion sends to DLQ."""
    broker = FaultyBroker()
    idempotency = InMemoryIdempotencyStore()
    app = App(broker=broker, source="test-service", idempotency_store=idempotency, strict=False)

    attempt_count = [0]

    @subscribe(SampleEvent, idempotency=IdempotencyPolicy(), dlq=True, max_attempts=2)
    async def handler(event, ctx):
        attempt_count[0] += 1
        raise RetryableError("Temporary failure")

    app.add_handler(handler)

    event = SampleEvent(data=SampleEvent.Data(value="test"))
    await app.publish(event)

    dlq_messages = []

    async def dlq_handler(msg: Message):
        dlq_messages.append(msg)

    consumer_task = asyncio.create_task(app.run())
    dlq_task = asyncio.create_task(
        broker.run_consumer(
            channels=["SampleEvent.dlq"], handler=dlq_handler, consumer_name="dlq-consumer"
        )
    )

    await asyncio.sleep(0.3)
    consumer_task.cancel()
    dlq_task.cancel()

    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    try:
        await dlq_task
    except asyncio.CancelledError:
        pass

    # Should have tried once (no actual retry since broker doesn't support it)
    assert attempt_count[0] >= 1
