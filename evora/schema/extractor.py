from __future__ import annotations

from typing import Any, Type

from evora.core import Event


def extract_schema(event_cls: Type[Event]) -> dict[str, Any]:
    if not hasattr(event_cls, "Data"):
        raise ValueError(f"{event_cls.__name__} does not define inner Data model")

    raw = event_cls.Data.model_json_schema()

    defs = raw.get("$defs", {})

    resolved = _resolve_refs(raw, defs)

    return _normalize_schema(resolved)


# -----------------------------------------
# $ref resolution
# -----------------------------------------


def _resolve_refs(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref.startswith("#/$defs/"):
            key = ref.split("/")[-1]
            return _resolve_refs(defs[key], defs)

    resolved: dict[str, Any] = {}

    for k, v in schema.items():
        if isinstance(v, dict):
            resolved[k] = _resolve_refs(v, defs)
        elif isinstance(v, list):
            resolved_list: list[Any] = [
                _resolve_refs(i, defs) if isinstance(i, dict) else i for i in v
            ]
            resolved[k] = resolved_list
        else:
            resolved[k] = v

    return resolved


# -----------------------------------------
# Structural normalization
# -----------------------------------------


def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}

    schema_type = schema.get("type")

    if isinstance(schema_type, list):
        result["type"] = sorted(schema_type)
    else:
        result["type"] = schema_type

    # Object
    if schema.get("type") == "object":
        result["properties"] = {}

        for name, prop in sorted(schema.get("properties", {}).items()):
            result["properties"][name] = _normalize_schema(prop)

        required = sorted(schema.get("required", []))
        if required:
            result["required"] = required

    # Array
    if schema.get("type") == "array":
        result["items"] = _normalize_schema(schema.get("items", {}))

    # Enum
    if "enum" in schema:
        result["enum"] = sorted(schema["enum"])

    return result
