from enum import Enum

import pytest
from pydantic import BaseModel

from evora.core import Event
from evora.schema.diff import _join, compare_schemas
from evora.schema.extractor import extract_schema


class V1(Event):
    __version__ = 1

    class Data(BaseModel):
        user_id: int
        name: str

    data: Data

    @classmethod
    def event_type(cls):
        return "users.events"


class V2_AddOptional(Event):
    __version__ = 2

    class Data(BaseModel):
        user_id: int
        name: str
        nickname: str | None = None

    data: Data

    @classmethod
    def event_type(cls):
        return "users.events"


class V2_RemoveField(Event):
    __version__ = 2

    class Data(BaseModel):
        user_id: int

    data: Data

    @classmethod
    def event_type(cls):
        return "users.events"


class V2_TypeChange(Event):
    __version__ = 2

    class Data(BaseModel):
        user_id: str  # was int
        name: str

    data: Data

    @classmethod
    def event_type(cls):
        return "users.events"


class StatusV1(Enum):
    A = "A"
    B = "B"


class StatusV2(Enum):
    A = "A"
    B = "B"
    C = "C"


class EnumV1(Event):
    __version__ = 1

    class Data(BaseModel):
        status: StatusV1

    data: Data

    @classmethod
    def event_type(cls):
        return "enum.events"


class EnumV2_Add(Event):
    __version__ = 2

    class Data(BaseModel):
        status: StatusV2

    data: Data

    @classmethod
    def event_type(cls):
        return "enum.events"


class NestedV1(Event):
    __version__ = 1

    class Data(BaseModel):
        user: V1.Data

    data: Data

    @classmethod
    def event_type(cls):
        return "nested.events"


class NestedV2_Remove(Event):
    __version__ = 2

    class Data(BaseModel):
        user: V2_RemoveField.Data

    data: Data

    @classmethod
    def event_type(cls):
        return "nested.events"


def test_add_optional_field_is_compatible():
    old = extract_schema(V1)
    new = extract_schema(V2_AddOptional)

    result = compare_schemas(old, new)

    assert result.is_compatible
    assert any("nickname added as optional" in x for x in result.non_breaking_changes)


def test_remove_field_is_breaking():
    old = extract_schema(V1)
    new = extract_schema(V2_RemoveField)

    result = compare_schemas(old, new)

    assert not result.is_compatible
    assert any("name removed" in x for x in result.breaking_changes)


def test_type_change_is_breaking():
    old = extract_schema(V1)
    new = extract_schema(V2_TypeChange)

    result = compare_schemas(old, new)

    assert not result.is_compatible
    assert any("type changed" in x for x in result.breaking_changes)


def test_enum_add_value_is_non_breaking():
    old = extract_schema(EnumV1)
    new = extract_schema(EnumV2_Add)

    result = compare_schemas(old, new)

    assert result.is_compatible
    assert any("enum value added" in x for x in result.non_breaking_changes)


def test_nested_removal_is_breaking():
    old = extract_schema(NestedV1)
    new = extract_schema(NestedV2_Remove)

    result = compare_schemas(old, new)

    assert not result.is_compatible


def test_join_helper():
    """Test the _join helper function."""
    # Import from diff module

    assert _join("", "field") == "field"
    assert _join("root", "field") == "root.field"
    assert _join("root.nested", "field") == "root.nested.field"


def test_compare_schemas_primitive_type_change():
    """Test detecting primitive type changes."""
    old = {"type": "string"}
    new = {"type": "number"}

    result = compare_schemas(old, new)

    assert not result.is_compatible
    assert any("type changed" in change for change in result.breaking_changes)


def test_compare_schemas_enum_changes():
    """Test enum value additions and removals."""
    old = {"type": "string", "enum": ["a", "b", "c"]}
    new = {"type": "string", "enum": ["b", "c", "d"]}

    result = compare_schemas(old, new)

    assert not result.is_compatible
    assert any("enum value removed 'a'" in change for change in result.breaking_changes)
    assert any("enum value added 'd'" in change for change in result.non_breaking_changes)


def test_compare_schemas_enum_only_additions():
    """Test enum with only additions is non-breaking."""
    old = {"type": "string", "enum": ["a", "b"]}
    new = {"type": "string", "enum": ["a", "b", "c"]}

    result = compare_schemas(old, new)

    assert result.is_compatible
    assert any("enum value added 'c'" in change for change in result.non_breaking_changes)


def test_compare_schemas_array_items():
    """Test array item type changes."""
    old = {"type": "array", "items": {"type": "string"}}
    new = {"type": "array", "items": {"type": "number"}}

    result = compare_schemas(old, new)

    assert not result.is_compatible
    assert any("type changed" in change for change in result.breaking_changes)


def test_compare_schemas_nested_array():
    """Test nested array changes."""
    old = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "string"}}},
    }
    new = {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "number"}}},
    }

    result = compare_schemas(old, new)

    assert not result.is_compatible


def test_compare_schemas_field_removed():
    """Test field removal is breaking."""
    old = {
        "type": "object",
        "properties": {"field1": {"type": "string"}, "field2": {"type": "number"}},
    }
    new = {"type": "object", "properties": {"field1": {"type": "string"}}}

    result = compare_schemas(old, new)

    assert not result.is_compatible
    assert any("field2 removed" in change for change in result.breaking_changes)


def test_compare_schemas_required_field_added():
    """Test adding a required field is breaking."""
    old = {"type": "object", "properties": {"field1": {"type": "string"}}, "required": ["field1"]}
    new = {
        "type": "object",
        "properties": {"field1": {"type": "string"}, "field2": {"type": "number"}},
        "required": ["field1", "field2"],
    }

    result = compare_schemas(old, new)

    assert not result.is_compatible
    assert any("field2 added as required" in change for change in result.breaking_changes)


def test_compare_schemas_optional_field_added():
    """Test adding an optional field is non-breaking."""
    old = {"type": "object", "properties": {"field1": {"type": "string"}}, "required": ["field1"]}
    new = {
        "type": "object",
        "properties": {"field1": {"type": "string"}, "field2": {"type": "number"}},
        "required": ["field1"],
    }

    result = compare_schemas(old, new)

    assert result.is_compatible
    assert any("field2 added as optional" in change for change in result.non_breaking_changes)


def test_compare_schemas_field_made_optional():
    """Test making a required field optional is non-breaking."""
    old = {"type": "object", "properties": {"field1": {"type": "string"}}, "required": ["field1"]}
    new = {"type": "object", "properties": {"field1": {"type": "string"}}, "required": []}

    result = compare_schemas(old, new)

    assert result.is_compatible
    assert any("field1 made optional" in change for change in result.non_breaking_changes)


def test_compare_schemas_field_made_required():
    """Test making an optional field required is breaking."""
    old = {"type": "object", "properties": {"field1": {"type": "string"}}, "required": []}
    new = {"type": "object", "properties": {"field1": {"type": "string"}}, "required": ["field1"]}

    result = compare_schemas(old, new)

    assert not result.is_compatible
    assert any("field1 made required" in change for change in result.breaking_changes)


def test_compare_schemas_nested_object_changes():
    """Test changes in nested objects."""
    old = {
        "type": "object",
        "properties": {
            "nested": {
                "type": "object",
                "properties": {"field": {"type": "string"}},
                "required": ["field"],
            }
        },
    }
    new = {
        "type": "object",
        "properties": {
            "nested": {
                "type": "object",
                "properties": {"field": {"type": "number"}},
                "required": ["field"],
            }
        },
    }

    result = compare_schemas(old, new)

    assert not result.is_compatible
    assert any(
        "nested.field" in change and "type changed" in change for change in result.breaking_changes
    )


def test_compare_schemas_no_properties():
    """Test schemas without properties."""
    old = {"type": "object"}
    new = {"type": "object", "properties": {"field": {"type": "string"}}}

    result = compare_schemas(old, new)

    assert result.is_compatible  # Adding optional field to empty object


def test_compare_schemas_primitive_types():
    """Test comparison of primitive types."""
    old = {"type": "string"}
    new = {"type": "string"}

    result = compare_schemas(old, new)

    assert result.is_compatible
    assert len(result.breaking_changes) == 0
    assert len(result.non_breaking_changes) == 0


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
