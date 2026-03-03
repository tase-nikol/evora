from pydantic import BaseModel
from evora.core import Event
from evora.schema.extractor import extract_schema
from evora.schema.diff import compare_schemas
from enum import Enum


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