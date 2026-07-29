"""Minimal JSON-Schema (draft 2020-12 subset) validator — stdlib only.

Covers exactly the keywords our schemas/ use: type (incl. unions + "null"), required,
properties, additionalProperties (bool), enum, items, minItems, minimum, maximum.
Annotation/format keywords ($schema, $id, title, description, default, format) are
intentionally ignored. This is NOT a general validator — it's enough to gate our typed
data contracts in CI without pulling in `jsonschema`. If contracts outgrow this subset,
swap in the real library.
"""
from __future__ import annotations

_TYPE_CHECKS = {
    "object": lambda x: isinstance(x, dict),
    "array": lambda x: isinstance(x, list),
    "string": lambda x: isinstance(x, str),
    "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
    "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
    "boolean": lambda x: isinstance(x, bool),
    "null": lambda x: x is None,
}


def _check_type(value, t) -> bool:
    types = t if isinstance(t, list) else [t]
    return any(_TYPE_CHECKS[tt](value) for tt in types)


def validate(instance, schema, path: str = "$") -> list[str]:
    """Return a list of human-readable error strings ([] = valid)."""
    errs: list[str] = []
    t = schema.get("type")
    if t is not None and not _check_type(instance, t):
        return [f"{path}: expected type {t}, got {type(instance).__name__}"]  # don't cascade on type mismatch
    if "enum" in schema and instance not in schema["enum"]:
        errs.append(f"{path}: {instance!r} not in enum {schema['enum']}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errs.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errs.append(f"{path}: {instance} > maximum {schema['maximum']}")
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in instance:
                errs.append(f"{path}: missing required '{req}'")
        if schema.get("additionalProperties") is False:
            for k in instance:
                if k not in props:
                    errs.append(f"{path}: additional property '{k}' not allowed")
        for k, v in instance.items():
            if k in props:
                errs.extend(validate(v, props[k], f"{path}.{k}"))
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errs.append(f"{path}: {len(instance)} items < minItems {schema['minItems']}")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(instance):
                errs.extend(validate(item, item_schema, f"{path}[{i}]"))
    return errs


def is_valid(instance, schema) -> bool:
    return not validate(instance, schema)
