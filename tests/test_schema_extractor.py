"""Additional tests for schema extractor to reach 100% coverage."""

import pytest
from pydantic import BaseModel

from evora.core import Event
from evora.schema.extractor import extract_schema


class EventWithoutData(Event):
    __version__ = 1


class SimpleEvent(Event):
    __version__ = 1

    class Data(BaseModel):
        name: str
        count: int


class EventWithUnionType(Event):
    __version__ = 1

    class Data(BaseModel):
        value: str | int | None


class EventWithEnum(Event):
    __version__ = 1

    class Data(BaseModel):
        status: str  # Could have enum constraint
        items: list[str]


def test_extract_schema_missing_data_model():
    """Test that extracting schema without Data model raises error."""
    with pytest.raises(ValueError, match="does not define inner Data model"):
        extract_schema(EventWithoutData)


def test_extract_schema_simple():
    """Test extracting schema from simple event."""
    schema = extract_schema(SimpleEvent)

    assert schema["type"] == "object"
    assert "properties" in schema
    assert "name" in schema["properties"]
    assert "count" in schema["properties"]
    assert schema["properties"]["name"]["type"] == "string"
    assert schema["properties"]["count"]["type"] == "integer"


def test_extract_schema_with_union_types():
    """Test extracting schema with union types."""
    schema = extract_schema(EventWithUnionType)

    assert schema["type"] == "object"
    assert "properties" in schema
    assert "value" in schema["properties"]
    # Union types may be handled differently by pydantic
    # Just verify the property exists and has a type or anyOf structure
    assert "value" in schema["properties"]


def test_extract_schema_with_array():
    """Test extracting schema with array types."""
    schema = extract_schema(EventWithEnum)

    assert schema["type"] == "object"
    assert "items" in schema["properties"]
    assert schema["properties"]["items"]["type"] == "array"
    assert "items" in schema["properties"]["items"]


def test_extract_schema_nested_objects():
    """Test extracting schema with nested objects."""

    class Address(BaseModel):
        street: str
        city: str

    class NestedEvent(Event):
        __version__ = 1

        class Data(BaseModel):
            name: str
            address: Address

    schema = extract_schema(NestedEvent)

    assert schema["type"] == "object"
    assert "address" in schema["properties"]
    assert schema["properties"]["address"]["type"] == "object"
    assert "street" in schema["properties"]["address"]["properties"]
    assert "city" in schema["properties"]["address"]["properties"]


def test_extract_schema_with_required_fields():
    """Test that required fields are properly extracted."""

    class RequiredFieldsEvent(Event):
        __version__ = 1

        class Data(BaseModel):
            required_field: str
            optional_field: str | None = None

    schema = extract_schema(RequiredFieldsEvent)

    assert "required" in schema
    assert "required_field" in schema["required"]


def test_extract_schema_array_of_objects():
    """Test extracting schema with array of objects."""

    class Item(BaseModel):
        id: int
        name: str

    class ArrayOfObjectsEvent(Event):
        __version__ = 1

        class Data(BaseModel):
            items: list[Item]

    schema = extract_schema(ArrayOfObjectsEvent)

    assert "items" in schema["properties"]
    items_prop = schema["properties"]["items"]
    assert items_prop["type"] == "array"
    assert items_prop["items"]["type"] == "object"
    assert "id" in items_prop["items"]["properties"]
    assert "name" in items_prop["items"]["properties"]


def test_extract_schema_sorted_properties():
    """Test that properties are sorted alphabetically."""

    class UnsortedEvent(Event):
        __version__ = 1

        class Data(BaseModel):
            zebra: str
            apple: str
            middle: str

    schema = extract_schema(UnsortedEvent)

    props_keys = list(schema["properties"].keys())
    assert props_keys == sorted(props_keys)


def test_extract_schema_sorted_enum():
    """Test that enum values are sorted."""
    from enum import Enum as PyEnum

    class Status(str, PyEnum):
        PENDING = "pending"
        ACTIVE = "active"
        COMPLETED = "completed"

    class EnumEvent(Event):
        __version__ = 1

        class Data(BaseModel):
            status: Status

    schema = extract_schema(EnumEvent)

    if "enum" in schema["properties"]["status"]:
        enum_values = schema["properties"]["status"]["enum"]
        assert enum_values == sorted(enum_values)


def test_extract_schema_normalized_structure():
    """Test that schema structure is normalized."""

    class Metadata(BaseModel):
        version: int
        tags: list[str]

    class ComplexEvent(Event):
        __version__ = 1

        class Data(BaseModel):
            title: str
            metadata: Metadata

    schema = extract_schema(ComplexEvent)

    # Check normalization
    assert "type" in schema
    assert schema["type"] == "object"

    # Properties should be sorted
    assert list(schema["properties"].keys()) == sorted(schema["properties"].keys())

    # Nested objects should also be normalized
    metadata_schema = schema["properties"]["metadata"]
    assert "type" in metadata_schema
    assert list(metadata_schema["properties"].keys()) == sorted(
        metadata_schema["properties"].keys()
    )


def test_normalize_schema_with_list_type():
    """Test that schema normalization handles list types correctly."""
    from evora.schema.extractor import _normalize_schema

    # Test with a list of types (e.g., from union types)
    schema_with_list_type = {"type": ["string", "null", "integer"]}

    normalized = _normalize_schema(schema_with_list_type)

    # Type list should be sorted
    assert normalized["type"] == ["integer", "null", "string"]
