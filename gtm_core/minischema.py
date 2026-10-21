"""Minimal JSON-Schema (draft 2020-12 subset) validator — stdlib only.

Promoted out of ``tests/contracts/minijsonschema.py`` on 2026-09-03 so that RUNTIME code can
validate against ``schemas/`` too, not only CI. The move is the point: ``schemas/shots.schema.json``
declared itself the video-script→video-render contract while nothing outside the test tree ever
read it, and 11 of 14 committed shot lists had drifted away from it unnoticed. A validator only
tests can reach is how a schema becomes dead weight.

There is exactly ONE implementation. ``tests/contracts/minijsonschema.py`` re-exports this module,
so the ~10 existing test importers are unchanged and no second copy can drift from this one.

Covers exactly the keywords our ``schemas/`` use: ``type`` (incl. unions + ``"null"``),
``required``, ``properties``, ``additionalProperties`` (bool OR subschema), ``enum``, ``items``,
``minItems``, ``minLength``, ``minimum``, ``maximum``, ``exclusiveMinimum``.
Annotation keywords (``$schema``, ``$id``, ``title``, ``description``, ``default``, ``format``) are
intentionally ignored. This is NOT a general validator — no ``$ref``, ``oneOf``, ``anyOf`` or
``allOf``. It is enough to gate our typed data contracts without pulling in ``jsonschema``, which
``gtm_core`` may not depend on. If a contract outgrows this subset, swap in the real library rather
than growing this file.
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


def _validate_number(instance, schema: dict, path: str) -> list[str]:
    errs: list[str] = []
    if "minimum" in schema and instance < schema["minimum"]:
        errs.append(f"{path}: {instance} < minimum {schema['minimum']}")
    if "maximum" in schema and instance > schema["maximum"]:
        errs.append(f"{path}: {instance} > maximum {schema['maximum']}")
    if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
        errs.append(f"{path}: {instance} <= exclusiveMinimum {schema['exclusiveMinimum']}")
    return errs


def _validate_object(instance: dict, schema: dict, path: str) -> list[str]:
    errs: list[str] = []
    props = schema.get("properties", {})
    for req in schema.get("required", []):
        if req not in instance:
            errs.append(f"{path}: missing required '{req}'")
    additional = schema.get("additionalProperties")
    for k, v in instance.items():
        if k in props:
            errs.extend(validate(v, props[k], f"{path}.{k}"))
        elif isinstance(additional, dict):
            # `additionalProperties` as a SUBSCHEMA: every key not named in `properties` is
            # validated against it. Needed by shots.schema.json's `identity_bindings`, whose
            # keys are role names — arbitrary, but each value is a typed binding object.
            errs.extend(validate(v, additional, f"{path}.{k}"))
        elif additional is False:
            errs.append(f"{path}: additional property '{k}' not allowed")
    return errs


def _validate_array(instance: list, schema: dict, path: str) -> list[str]:
    errs: list[str] = []
    if "minItems" in schema and len(instance) < schema["minItems"]:
        errs.append(f"{path}: {len(instance)} items < minItems {schema['minItems']}")
    item_schema = schema.get("items")
    if item_schema:
        for i, item in enumerate(instance):
            errs.extend(validate(item, item_schema, f"{path}[{i}]"))
    return errs


def validate(instance, schema, path: str = "$") -> list[str]:
    """Return a list of human-readable error strings (``[]`` = valid)."""
    errs: list[str] = []
    t = schema.get("type")
    if t is not None and not _check_type(instance, t):
        # Don't cascade on a type mismatch — every downstream keyword would misfire.
        return [f"{path}: expected type {t}, got {type(instance).__name__}"]
    if "enum" in schema and instance not in schema["enum"]:
        errs.append(f"{path}: {instance!r} not in enum {schema['enum']}")
    if isinstance(instance, str) and "minLength" in schema and len(instance) < schema["minLength"]:
        errs.append(f"{path}: {len(instance)} chars < minLength {schema['minLength']}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        errs.extend(_validate_number(instance, schema, path))
    if isinstance(instance, dict):
        errs.extend(_validate_object(instance, schema, path))
    if isinstance(instance, list):
        errs.extend(_validate_array(instance, schema, path))
    return errs


def is_valid(instance, schema) -> bool:
    return not validate(instance, schema)
