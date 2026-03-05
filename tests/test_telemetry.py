from dataclasses import dataclass
from typing import Any

import pytest

from evora.app import App, subscribe
from evora.core import Event
from evora.errors import FatalError, RetryableError
from evora.idempotency import IdempotencyPolicy
from evora.observability.telemetry import NoopTelemetry

# ==========================================================
# Spy Telemetry
# ==========================================================


class SpyTelemetry:
    def __init__(self):
        self.events = []

    def on_consume_start(self, **kwargs):
        self.events.append(("start", kwargs))
        return "token"

    def on_consume_end(self, token, *, outcome, error=None):
        self.events.append(("end", {"outcome": outcome, "error": error}))

    def on_retry_scheduled(self, **kwargs):
        self.events.append(("retry", kwargs))

    def on_publish(self, **kwargs):
        self.events.append(("publish", kwargs))


# ==========================================================
# Fake Broker + Idempotency
# ==========================================================


class FakeBroker:
    def __init__(self):
        self.published = []
        self.scheduled_retries = []

    async def publish(self, channel, *, value, key=None, headers=None):
        self.published.append(channel)

    async def schedule_retry(self, **kwargs):
        self.scheduled_retries.append(kwargs)

    async def run_consumer(self, channels, handler, consumer_name):
        pass  # not needed for unit tests


class FakeIdempotencyStore:
    def __init__(self, already_seen=False):
        self.already_seen = already_seen
        self.marked = []

    async def seen(self, scope, event_id):
        return self.already_seen

    async def mark_seen(self, scope, event_id, ttl_seconds):
        self.marked.append(event_id)


# ==========================================================
# Test Event
# ==========================================================


class TestEvent(Event):
    __version__ = 1

    class Data:
        pass

    data: Any = None

    @classmethod
    def event_type(cls) -> str:
        return "test.events"


# ==========================================================
# Helpers
# ==========================================================


@dataclass
class FakeMessage:
    channel: str
    value: bytes
    headers: dict
    key: str | None
    message_id: str
    attempt: int = 1


def make_app(handler_fn, *, telemetry, idempotency_store):
    broker = FakeBroker()
    app = App(
        broker=broker,
        source="test-service",
        idempotency_store=idempotency_store,
        telemetry=telemetry,
        strict=True,
    )
    app.add_handler(handler_fn)
    return app, broker


def encode_event(app, event):
    envelope = event.to_envelope(source="test")
    return app.registry.encode(envelope)


# ==========================================================
# 1️⃣ Success
# ==========================================================


@pytest.mark.asyncio
async def test_success_calls_telemetry():
    telemetry = SpyTelemetry()
    idempotency = FakeIdempotencyStore()

    @subscribe(TestEvent, idempotency=IdempotencyPolicy(mode="event_id"))
    async def handler(event, ctx):
        return None

    app, broker = make_app(handler, telemetry=telemetry, idempotency_store=idempotency)

    raw = encode_event(app, TestEvent())
    msg = FakeMessage("test.events", raw, {}, None, "1")

    await app._handle_message(msg)

    assert any(e[0] == "start" for e in telemetry.events)
    assert any(e[0] == "end" and e[1]["outcome"] == "success" for e in telemetry.events)


# ==========================================================
# 2️⃣ Retry
# ==========================================================


@pytest.mark.asyncio
async def test_retry_triggers_retry_hook():
    telemetry = SpyTelemetry()
    idempotency = FakeIdempotencyStore()

    @subscribe(
        TestEvent,
        retry="exponential",
        max_attempts=3,
        idempotency=IdempotencyPolicy(mode="event_id"),
    )
    async def handler(event, ctx):
        raise RetryableError("temporary")

    app, broker = make_app(handler, telemetry=telemetry, idempotency_store=idempotency)

    raw = encode_event(app, TestEvent())
    msg = FakeMessage("test.events", raw, {}, None, "1")

    await app._handle_message(msg)

    assert broker.scheduled_retries
    assert any(e[0] == "retry" for e in telemetry.events)
    assert any(e[0] == "end" and e[1]["outcome"] == "retry" for e in telemetry.events)


# ==========================================================
# 3️⃣ Fatal → DLQ
# ==========================================================


@pytest.mark.asyncio
async def test_fatal_error_goes_to_dlq():
    telemetry = SpyTelemetry()
    idempotency = FakeIdempotencyStore()

    @subscribe(TestEvent, idempotency=IdempotencyPolicy(mode="event_id"))
    async def handler(event, ctx):
        raise FatalError("boom")

    app, broker = make_app(handler, telemetry=telemetry, idempotency_store=idempotency)

    raw = encode_event(app, TestEvent())
    msg = FakeMessage("test.events", raw, {}, None, "1")

    await app._handle_message(msg)

    assert any(".dlq" in ch for ch in broker.published)
    assert any(e[0] == "end" and e[1]["outcome"] == "dlq" for e in telemetry.events)


# ==========================================================
# 4️⃣ Idempotency Skip
# ==========================================================


@pytest.mark.asyncio
async def test_idempotency_skip():
    telemetry = SpyTelemetry()
    idempotency = FakeIdempotencyStore(already_seen=True)

    @subscribe(TestEvent, idempotency=IdempotencyPolicy(mode="event_id"))
    async def handler(event, ctx):
        return None

    app, broker = make_app(handler, telemetry=telemetry, idempotency_store=idempotency)

    raw = encode_event(app, TestEvent())
    msg = FakeMessage("test.events", raw, {}, None, "1")

    await app._handle_message(msg)

    assert any(e[0] == "end" and e[1]["outcome"] == "skip" for e in telemetry.events)


# ==========================================================
# 5️⃣ Retry Exhaustion → DLQ
# ==========================================================


@pytest.mark.asyncio
async def test_retry_exhaustion_goes_to_dlq():
    telemetry = SpyTelemetry()
    idempotency = FakeIdempotencyStore()

    @subscribe(
        TestEvent,
        retry="exponential",
        max_attempts=1,
        idempotency=IdempotencyPolicy(mode="event_id"),
    )
    async def handler(event, ctx):
        raise RetryableError("fail")

    app, broker = make_app(handler, telemetry=telemetry, idempotency_store=idempotency)

    raw = encode_event(app, TestEvent())
    msg = FakeMessage("test.events", raw, {}, None, "1")

    await app._handle_message(msg)

    assert any(".dlq" in ch for ch in broker.published)
    assert any(e[0] == "end" and e[1]["outcome"] == "dlq" for e in telemetry.events)


# ==========================================================
# 6️⃣ Publish Telemetry
# ==========================================================


@pytest.mark.asyncio
async def test_publish_calls_telemetry():
    telemetry = SpyTelemetry()
    idempotency = FakeIdempotencyStore()

    @subscribe(TestEvent, idempotency=IdempotencyPolicy(mode="event_id"))
    async def handler(event, ctx):
        return None

    app, broker = make_app(handler, telemetry=telemetry, idempotency_store=idempotency)

    await app.publish(TestEvent())

    assert any(e[0] == "publish" for e in telemetry.events)


def test_noop_telemetry_on_consume_start():
    """Test NoopTelemetry.on_consume_start returns None."""
    telemetry = NoopTelemetry()

    token = telemetry.on_consume_start(
        service="test-service",
        event_type="TestEvent",
        handler="test.handler",
        event_id="event-123",
        attempt=1,
        attrs={"channel": "test-channel"},
    )

    assert token is None


def test_noop_telemetry_on_consume_end():
    """Test NoopTelemetry.on_consume_end returns None."""
    telemetry = NoopTelemetry()

    result = telemetry.on_consume_end(
        token=None,
        outcome="success",
        error=None,
    )

    assert result is None


def test_noop_telemetry_on_consume_end_with_error():
    """Test NoopTelemetry.on_consume_end with error returns None."""
    telemetry = NoopTelemetry()

    result = telemetry.on_consume_end(
        token=None,
        outcome="error",
        error=ValueError("test error"),
    )

    assert result is None


def test_noop_telemetry_on_retry_scheduled():
    """Test NoopTelemetry.on_retry_scheduled returns None."""
    telemetry = NoopTelemetry()

    result = telemetry.on_retry_scheduled(
        service="test-service",
        event_type="TestEvent",
        handler="test.handler",
        event_id="event-123",
        attempt=1,
        next_attempt=2,
    )

    assert result is None


def test_noop_telemetry_on_publish():
    """Test NoopTelemetry.on_publish returns None."""
    telemetry = NoopTelemetry()

    result = telemetry.on_publish(
        service="test-service",
        event_type="TestEvent",
        channel="test-channel",
    )

    assert result is None


def test_noop_telemetry_all_outcomes():
    """Test NoopTelemetry handles all outcome types."""
    telemetry = NoopTelemetry()

    outcomes = ["success", "retry", "dlq", "skip", "error"]

    for outcome in outcomes:
        result = telemetry.on_consume_end(
            token=None,
            outcome=outcome,
            error=None,
        )
        assert result is None
