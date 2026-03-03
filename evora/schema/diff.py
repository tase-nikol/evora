from __future__ import annotations

from typing import Any
from .compatibility import CompatibilityResult


def compare_schemas(old: dict[str, Any], new: dict[str, Any]) -> CompatibilityResult:
    breaking = []
    non_breaking = []

    _compare_objects(old, new, path="", breaking=breaking, non_breaking=non_breaking)

    return CompatibilityResult(
        is_compatible=len(breaking) == 0,
        breaking_changes=breaking,
        non_breaking_changes=non_breaking,
    )


def _compare_objects(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    path: str,
    breaking: list[str],
    non_breaking: list[str],
) -> None:

    if old.get("type") != new.get("type"):
        breaking.append(
            f"{path or 'root'}: type changed {old.get('type')} → {new.get('type')}"
        )
        return

    # Enum comparison
    if "enum" in old or "enum" in new:
        old_enum = set(old.get("enum", []))
        new_enum = set(new.get("enum", []))

        removed = old_enum - new_enum
        added = new_enum - old_enum

        for value in removed:
            breaking.append(f"{path}: enum value removed '{value}'")

        for value in added:
            non_breaking.append(f"{path}: enum value added '{value}'")

    # Array comparison
    if old.get("type") == "array":
        _compare_objects(
            old.get("items", {}),
            new.get("items", {}),
            path=f"{path}[]",
            breaking=breaking,
            non_breaking=non_breaking,
        )
        return

    if old.get("type") != "object":
        # primitive type handled above
        return

    old_props = old.get("properties", {})
    new_props = new.get("properties", {})

    old_required = set(old.get("required", []))
    new_required = set(new.get("required", []))

    # Removed fields → breaking
    for field in old_props:
        if field not in new_props:
            breaking.append(f"{_join(path, field)} removed")

    # Added fields
    for field in new_props:
        if field not in old_props:
            if field in new_required:
                breaking.append(f"{_join(path, field)} added as required")
            else:
                non_breaking.append(f"{_join(path, field)} added as optional")

    # Compare shared fields
    for field in old_props:
        if field in new_props:
            field_path = _join(path, field)

            # Required change
            if field in old_required and field not in new_required:
                non_breaking.append(f"{field_path} made optional")

            if field not in old_required and field in new_required:
                breaking.append(f"{field_path} made required")

            # Recurse
            _compare_objects(
                old_props[field],
                new_props[field],
                path=field_path,
                breaking=breaking,
                non_breaking=non_breaking,
            )


def _join(path: str, field: str) -> str:
    return f"{path}.{field}" if path else field