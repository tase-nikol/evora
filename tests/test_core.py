"""Tests for core Event and Registry functionality."""

import pytest
from pydantic import BaseModel

from evora.core import Envelope, Event, Registry


class SampleEvent(Event):
    __version__ = 1

    class Data(BaseModel):
        name: str
        count: int

    data: Data


class AnotherEvent(Event):
    __event_name__ = "CustomEventName"
    __version__ = 2

    class Data(BaseModel):
        value: str

    data: Data


def test_event_type_uses_class_name():
    """Test that event_type() returns class name by default."""
    assert SampleEvent.event_type() == "SampleEvent"


def test_event_type_uses_custom_name():
    """Test that event_type() returns __event_name__ if set."""
    assert AnotherEvent.event_type() == "CustomEventName"


def test_schema_uri():
    """Test schema URI generation."""
    assert SampleEvent.schema_uri() == "evora://SampleEvent/1"
    assert AnotherEvent.schema_uri() == "evora://CustomEventName/2"


def test_to_envelope_minimal():
    """Test envelope creation with minimal parameters."""
    event = SampleEvent(data=SampleEvent.Data(name="test", count=42))
    envelope = event.to_envelope(source="test-service")

    assert envelope.specversion == "1.0"
    assert envelope.type == "SampleEvent"
    assert envelope.source == "test-service"
    assert envelope.subject is None
    assert envelope.dataschema == "evora://SampleEvent/1"
    assert envelope.traceparent is None
    assert envelope.data == {"data": {"name": "test", "count": 42}}
    assert envelope.meta == {"schema_version": 1}
    assert envelope.id is not None  # Should be auto-generated UUID


def test_to_envelope_with_all_params():
    """Test envelope creation with all parameters."""
    event = SampleEvent(data=SampleEvent.Data(name="test", count=42))
    envelope = event.to_envelope(
        source="test-service",
        subject="user:123",
        traceparent="00-abc123-def456-01",
        meta={"partition_key": "pk123", "custom": "value"},
        event_id="custom-event-id",
    )

    assert envelope.id == "custom-event-id"
    assert envelope.type == "SampleEvent"
    assert envelope.source == "test-service"
    assert envelope.subject == "user:123"
    assert envelope.traceparent == "00-abc123-def456-01"
    assert envelope.data == {"data": {"name": "test", "count": 42}}
    assert envelope.meta == {"schema_version": 1, "partition_key": "pk123", "custom": "value"}


def test_registry_register_and_resolve():
    """Test registering and resolving events in registry."""
    registry = Registry()
    registry.register(SampleEvent)
    registry.register(AnotherEvent)

    resolved = registry.resolve("SampleEvent")
    assert resolved == SampleEvent

    resolved = registry.resolve("CustomEventName")
    assert resolved == AnotherEvent


def test_registry_resolve_unregistered():
    """Test resolving an unregistered event type raises KeyError."""
    registry = Registry()

    with pytest.raises(KeyError, match="Unregistered event type: NonExistent"):
        registry.resolve("NonExistent")


def test_registry_decode():
    """Test decoding a raw message into envelope and event."""
    registry = Registry()
    registry.register(SampleEvent)

    # Create an event and envelope
    event = SampleEvent(data=SampleEvent.Data(name="test", count=99))
    envelope = event.to_envelope(source="test-service", event_id="test-id-123")

    # Encode to bytes
    raw = envelope.model_dump_json().encode()

    # Decode
    decoded_envelope, decoded_event = registry.decode(raw)

    assert decoded_envelope.id == "test-id-123"
    assert decoded_envelope.type == "SampleEvent"
    assert decoded_envelope.source == "test-service"
    assert isinstance(decoded_event, SampleEvent)
    assert decoded_event.data.name == "test"
    assert decoded_event.data.count == 99


def test_envelope_model():
    """Test Envelope model validation."""
    envelope = Envelope(
        id="test-id",
        type="TestEvent",
        source="test-service",
        time="2026-03-05T10:00:00Z",
        dataschema="evora://TestEvent/1",
        data={"key": "value"},
    )

    assert envelope.specversion == "1.0"
    assert envelope.datacontenttype == "application/json"
    assert envelope.meta == {}


def test_envelope_with_meta():
    """Test Envelope with metadata."""
    envelope = Envelope(
        id="test-id",
        type="TestEvent",
        source="test-service",
        time="2026-03-05T10:00:00Z",
        dataschema="evora://TestEvent/1",
        data={"key": "value"},
        meta={"attempt": 2, "custom": "info"},
    )

    assert envelope.meta == {"attempt": 2, "custom": "info"}
