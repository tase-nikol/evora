from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import sys
from typing import Any, Tuple

from evora.schema.diff import compare_schemas
from evora.schema.extractor import extract_schema


def main() -> int:
    parser = argparse.ArgumentParser(prog="evora")
    sub = parser.add_subparsers(dest="command")

    schema_parser = sub.add_parser("schema")
    schema_sub = schema_parser.add_subparsers(dest="schema_command")

    # evora schema check <old> <new>
    check_parser = schema_sub.add_parser("check", help="Check schema compatibility")
    check_parser.add_argument("old", help="module:Event or file.py:Event or baseline.json")
    check_parser.add_argument("new", help="module:Event or file.py:Event")
    check_parser.add_argument("--format", choices=["text", "json"], default="text")
    check_parser.add_argument(
        "--require-version-bump",
        action="store_true",
        default=True,
        help="Fail if breaking changes without __version__ increase (default: true)",
    )
    check_parser.add_argument(
        "--no-require-version-bump", dest="require_version_bump", action="store_false"
    )
    check_parser.add_argument(
        "--enforce-event-type",
        action="store_true",
        default=True,
        help="Fail if event_type differs (default: true)",
    )
    check_parser.add_argument(
        "--no-enforce-event-type", dest="enforce_event_type", action="store_false"
    )

    # evora schema export <ref>
    export_parser = schema_sub.add_parser("export", help="Export normalized schema to JSON")
    export_parser.add_argument("ref", help="module:Event or file.py:Event")
    export_parser.add_argument("--out", "-o", default="-", help="Output file (default: stdout)")

    args = parser.parse_args()

    if args.command == "schema" and args.schema_command == "check":
        return handle_schema_check(
            old_ref=args.old,
            new_ref=args.new,
            fmt=args.format,
            require_version_bump=args.require_version_bump,
            enforce_event_type=args.enforce_event_type,
        )

    if args.command == "schema" and args.schema_command == "export":
        return handle_schema_export(args.ref, args.out)

    parser.print_help()
    return 1


# ---------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------


def handle_schema_export(ref: str, out: str) -> int:
    try:
        event_cls, meta = _load_event_or_baseline(ref, allow_baseline=False)
        schema = extract_schema(event_cls)

        payload = {
            "kind": "evora.schema",
            "event_type": meta["event_type"],
            "version": meta["version"],
            "schema": schema,
        }

        raw = json.dumps(payload, indent=2, sort_keys=True)

        if out == "-" or out.lower() == "stdout":
            print(raw)
        else:
            with open(out, "w", encoding="utf-8") as f:
                f.write(raw + "\n")

        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def handle_schema_check(
    *,
    old_ref: str,
    new_ref: str,
    fmt: str,
    require_version_bump: bool,
    enforce_event_type: bool,
) -> int:
    try:
        old_event, old_meta = _load_event_or_baseline(old_ref, allow_baseline=True)
        new_event, new_meta = _load_event_or_baseline(new_ref, allow_baseline=False)

        # Event identity enforcement
        if enforce_event_type and old_meta["event_type"] != new_meta["event_type"]:
            return _emit(
                fmt,
                ok=False,
                error=f"event_type mismatch: {old_meta['event_type']} vs {new_meta['event_type']}",
                breaking=[],
                non_breaking=[],
                old_meta=old_meta,
                new_meta=new_meta,
            )

        old_schema = old_meta.get("schema") or extract_schema(old_event)
        new_schema = extract_schema(new_event)

        result = compare_schemas(old_schema, new_schema)

        # Version enforcement
        version_error = None
        if require_version_bump and (not result.is_compatible):
            if new_meta["version"] <= old_meta["version"]:
                version_error = (
                    f"breaking change requires __version__ bump: "
                    f"old={old_meta['version']} new={new_meta['version']}"
                )

        ok = result.is_compatible and (version_error is None)

        return _emit(
            fmt,
            ok=ok,
            error=version_error,
            breaking=result.breaking_changes,
            non_breaking=result.non_breaking_changes,
            old_meta=old_meta,
            new_meta=new_meta,
        )

    except Exception as e:
        return _emit(
            fmt,
            ok=False,
            error=str(e),
            breaking=[],
            non_breaking=[],
            old_meta=None,
            new_meta=None,
        )


# ---------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------


def _emit(
    fmt: str,
    *,
    ok: bool,
    error: str | None,
    breaking: list[str],
    non_breaking: list[str],
    old_meta: dict[str, Any] | None,
    new_meta: dict[str, Any] | None,
) -> int:
    if fmt == "json":
        payload = {
            "ok": ok,
            "error": error,
            "old": old_meta,
            "new": new_meta,
            "breaking_changes": breaking,
            "non_breaking_changes": non_breaking,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if ok else 1

    # text output
    if error:
        print(f"❌ {error}\n")

    if ok:
        print("✅ BACKWARD COMPATIBLE\n")
        if non_breaking:
            print("Non-breaking changes:")
            for c in non_breaking:
                print(f"  - {c}")
    else:
        print("❌ BREAKING CHANGES DETECTED\n")
        for c in breaking:
            print(f"  - {c}")
        if non_breaking:
            print("\nNon-breaking changes:")
            for c in non_breaking:
                print(f"  - {c}")
        print("\nCompatibility: NOT BACKWARD COMPATIBLE")

    return 0 if ok else 1


# ---------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------


def _load_event_or_baseline(ref: str, *, allow_baseline: bool) -> Tuple[Any, dict[str, Any]]:
    # Baseline JSON
    if allow_baseline and ref.endswith(".json") and os.path.isfile(ref):
        with open(ref, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("kind") != "evora.schema":
            raise ValueError("Not an Evora schema baseline JSON (kind != evora.schema)")

        meta = {
            "event_type": payload["event_type"],
            "version": int(payload["version"]),
            "schema": payload["schema"],
            "source": ref,
        }
        return None, meta

    # Otherwise load class
    event_cls = _load_event(ref)
    meta = {
        "event_type": event_cls.event_type(),
        "version": int(getattr(event_cls, "__version__", 0) or 0),
        "schema": None,
        "source": ref,
    }
    return event_cls, meta


def _load_event(ref: str):
    """
    Load event class from:
      1) module.path:ClassName
      2) path/to/file.py:ClassName
    """
    if ":" not in ref:
        raise ValueError("Event reference must be module.path:ClassName or file.py:ClassName")

    module_or_path, class_name = ref.split(":", 1)

    # file path
    if module_or_path.endswith(".py") or os.path.isfile(module_or_path):
        return _load_from_file(module_or_path, class_name)

    # module path
    module = importlib.import_module(module_or_path)
    if not hasattr(module, class_name):
        raise AttributeError(f"{class_name} not found in module {module_or_path}")
    return getattr(module, class_name)


def _load_from_file(path: str, class_name: str):
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    module_name = os.path.splitext(os.path.basename(path))[0]

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    if not hasattr(module, class_name):
        raise AttributeError(f"{class_name} not found in {path}")
    return getattr(module, class_name)


if __name__ == "__main__":
    raise SystemExit(main())
