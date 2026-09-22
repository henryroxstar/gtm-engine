"""The four hardcoded closed sets that spell the engine's `external_effect` vocabulary.

`gtm_core.packs.loader._ALLOWED_EXTERNAL_EFFECTS` is the one people think of. It is one of
FOUR, and the other three are easy to miss because none of them imports it:

  1. the loader's set (the source of truth);
  2. `backend/services/runs/gate_kinds.dispatch_target` — which effects a gate approval
     can dispatch at all;
  3. the `run_gates.gate` SQL CHECK — the newest migration that widens it;
  4. `schemas/run-event.schema.json` — the `gate` enum a client validates against.

Widening three of four is not a partial rollout: it is a **silent approve-and-skip**. The
operator approves a gate; the dispatcher does nothing, or the INSERT recording the decision
fails on a constraint. This test derives the other three FROM the loader so the next
widening cannot repeat the omission — which is why it asserts agreement rather than
restating the member list in a fifth place.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from gtm_core.packs.loader import _ALLOWED_EXTERNAL_EFFECTS

REPO = Path(__file__).resolve().parents[2]

#: Gate kinds that are NOT external effects — a pause with no dispatch behind it.
_NON_EFFECT_GATES = frozenset({"plan", "review"})


def test_the_loader_set_is_the_expected_three():
    """Changed deliberately on 2026-09-21 (SC9), authorized by the sequencer-capability
    PRD §9.2. A fourth value must still be argued for in a PRD, not added here."""
    assert _ALLOWED_EXTERNAL_EFFECTS == frozenset({"publish", "email_enroll", "dnc_add"})


def test_no_removal_effect_exists():
    """Add-only, structurally. Nothing in this system may un-suppress a person who opted
    out, so a removal effect must not be representable at all."""
    for forbidden in ("dnc_remove", "dnc_delete", "unsuppress", "prospect_pause"):
        assert forbidden not in _ALLOWED_EXTERNAL_EFFECTS


def test_the_dispatcher_lists_exactly_the_loader_set():
    """`dispatch_target` spells the effect names out instead of importing them, because
    `gate_kinds` is a deliberate pure leaf — both the real executors and the fake call it,
    and an import there is an import cycle waiting to happen
    (`test_the_shared_gate_kind_rules_are_a_pure_leaf`).

    So the duplication is load-bearing, and THIS is what keeps the two in step. Parsed from
    source rather than imported, for the same reason the module does not import.
    """
    source = (REPO / "backend/services/runs/gate_kinds.py").read_text(encoding="utf-8")
    fn = next(
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.FunctionDef) and n.name == "dispatch_target"
    )
    literals = {
        elt.value
        for node in ast.walk(fn)
        if isinstance(node, (ast.Tuple, ast.Set, ast.List))
        for elt in node.elts
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
    }
    assert literals == set(_ALLOWED_EXTERNAL_EFFECTS), (
        f"dispatch_target lists {sorted(literals)} but the loader allows "
        f"{sorted(_ALLOWED_EXTERNAL_EFFECTS)} — an approved gate for the difference would "
        "dispatch nothing at all"
    )


def test_the_draft_kind_map_covers_every_effect():
    """`pack_gate_kind` turns a draft kind into a gate kind. Every effect needs one, or an
    approved gate records the wrong kind on its durable row."""
    from backend.services.runs.gate_kinds import pack_gate_kind

    produced = {pack_gate_kind(k) for k in ("plan", "enroll", "publish", "dnc", None)}
    missing = _ALLOWED_EXTERNAL_EFFECTS - produced
    assert not missing, f"no draft kind maps to {sorted(missing)}"


def _newest_gate_check() -> set[str]:
    """The gate values admitted by the newest `run_gates` CHECK migration."""
    migrations = sorted((REPO / "backend/schema").glob("V*__*.sql"))
    newest: set[str] | None = None
    for path in migrations:
        text = path.read_text(encoding="utf-8")
        match = re.search(r"CHECK\s*\(gate\s+IN\s*\(([^)]*)\)\)", text, re.IGNORECASE)
        if match:
            newest = {v.strip().strip("'\"") for v in match.group(1).split(",") if v.strip()}
    assert newest is not None, "no run_gates gate CHECK found in any migration"
    return newest


def test_the_sql_check_admits_every_effect():
    admitted = _newest_gate_check()
    missing = _ALLOWED_EXTERNAL_EFFECTS - admitted
    assert not missing, (
        f"the newest run_gates CHECK does not admit {sorted(missing)} — an operator could "
        "approve that gate and the INSERT recording the decision would fail"
    )
    assert _NON_EFFECT_GATES <= admitted


def test_the_run_event_schema_admits_every_effect():
    schema = json.loads((REPO / "schemas/run-event.schema.json").read_text(encoding="utf-8"))
    enums = [
        node["enum"]
        for node in _walk(schema)
        if isinstance(node, dict) and isinstance(node.get("enum"), list) and "plan" in node["enum"]
    ]
    assert enums, "no gate enum found in the run-event schema"
    for enum in enums:
        missing = _ALLOWED_EXTERNAL_EFFECTS - set(enum)
        assert not missing, f"run-event gate enum does not admit {sorted(missing)}"


def test_all_four_sets_agree():
    """The single assertion this file exists for."""
    sql = _newest_gate_check()
    schema = json.loads((REPO / "schemas/run-event.schema.json").read_text(encoding="utf-8"))
    schema_enums = {
        frozenset(node["enum"])
        for node in _walk(schema)
        if isinstance(node, dict) and isinstance(node.get("enum"), list) and "plan" in node["enum"]
    }
    for enum in schema_enums:
        assert (sql - _NON_EFFECT_GATES) == (set(enum) - _NON_EFFECT_GATES), (
            f"SQL CHECK {sorted(sql)} and schema enum {sorted(enum)} disagree"
        )
    assert _ALLOWED_EXTERNAL_EFFECTS == (sql - _NON_EFFECT_GATES)


def _walk(node):
    """Yield every dict in a nested JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_a_gate_with_two_differently_effected_successors_is_refused():
    """SC9. One approval promotes ONE draft, and the draft's shape says which effect it is
    for. Two differently-effected successors would hand that draft to two dispatchers — and
    the VPS path (which loops) and the backend path (which takes the first match) would not
    even agree on which one ran. Refused at load, so no pack can be written that way."""
    import pytest

    from gtm_core.packs.loader import PackValidationError, _build_node, validate_node_semantics

    nodes = (
        _build_node({"id": "review", "gate": True, "model_role": "brain_plan"}),
        _build_node(
            {
                "id": "enroll",
                "depends_on": ["review"],
                "gate": True,
                "external_effect": "email_enroll",
                "model_role": "brain_plan",
            }
        ),
        _build_node(
            {
                "id": "suppress",
                "depends_on": ["review"],
                "gate": True,
                "external_effect": "dnc_add",
                "model_role": "brain_plan",
            }
        ),
    )
    with pytest.raises(PackValidationError) as exc:
        validate_node_semantics(nodes)
    assert exc.value.rule == "ambiguous_gate_dispatch"


def test_a_gate_with_one_effected_successor_is_still_fine():
    """The positive control: the shipped shape must keep loading."""
    from gtm_core.packs.loader import _build_node, validate_node_semantics

    nodes = (
        _build_node({"id": "review", "gate": True, "model_role": "brain_plan"}),
        _build_node(
            {
                "id": "suppress",
                "depends_on": ["review"],
                "gate": True,
                "external_effect": "dnc_add",
                "model_role": "brain_plan",
            }
        ),
    )
    validate_node_semantics(nodes)  # does not raise
