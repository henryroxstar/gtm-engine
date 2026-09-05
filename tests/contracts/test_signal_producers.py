"""Contract: every mapped signal_type is either PRODUCED or explicitly declared unproduced.

This repo's recurring failure shape is a correct mechanism with nothing feeding it — the
2026-08-27 wiring-gap PRD counted it to seven. `gtm_core.signals.SUGGESTED_ACTIONS` was
instance #4: shipped 2026-07-21, consumers none. W4 gave its *actions* a consumer
(`agent.signal_dispatch.ACTION_DISPATCH`), which closed the downstream half — and left the
upstream half open, because eight of the ten types were emitted by nothing.

This file closes the upstream half the same way `ACTION_DISPATCH` closed the downstream
one: with an explicit "decided against" that reads differently from "forgotten". An
eleventh signal_type added with no producer and no entry in `UNPRODUCED_SIGNAL_TYPES`
fails here, at CI, instead of being discovered by an audit months later.

The producer set is read from each producer's own declared constant rather than by
AST-scanning for `build_signal` calls: the live call site
(`agent/optout_sweep.py`) passes a *variable* (`classify_reply(...)`), so a literal scan
would report zero producers and pass vacuously.
"""

from __future__ import annotations

import ast
from pathlib import Path

from gtm_core.reply_classify import CLASSIFIED_TYPES
from gtm_core.signals import SUGGESTED_ACTIONS, UNPRODUCED_SIGNAL_TYPES

REPO = Path(__file__).resolve().parents[2]

#: Every declared producer's own type set. Add a module here when it starts emitting.
PRODUCED_TYPES = frozenset(CLASSIFIED_TYPES)


def test_produced_and_unproduced_exactly_partition_the_map():
    """No type may be both, and none may be neither."""
    mapped = set(SUGGESTED_ACTIONS)
    produced = set(PRODUCED_TYPES)
    declared_unproduced = set(UNPRODUCED_SIGNAL_TYPES)

    both = sorted(produced & declared_unproduced)
    assert not both, f"{both} are declared unproduced but a producer emits them"

    neither = sorted(mapped - produced - declared_unproduced)
    assert not neither, (
        f"{neither} are mapped to an action, emitted by nothing, and not declared in "
        "gtm_core.signals.UNPRODUCED_SIGNAL_TYPES. That is a new instance of the wiring "
        "gap. Either give it a producer or record the decision (with the reason) there."
    )

    stale = sorted(declared_unproduced - mapped)
    assert not stale, f"{stale} are declared unproduced but no longer appear in SUGGESTED_ACTIONS"


def test_every_unproduced_entry_carries_a_reason():
    """A bare set would let 'forgotten' hide inside 'decided against'."""
    empty = sorted(k for k, v in UNPRODUCED_SIGNAL_TYPES.items() if not (v or "").strip())
    assert not empty, f"{empty} are declared unproduced with no reason recorded"


def test_the_producer_is_actually_wired_into_the_sweep():
    """CLASSIFIED_TYPES declaring a producer is worthless if nothing calls it.

    AST, not text: a mention inside a docstring or a comment is not a call.
    """
    src = (REPO / "agent" / "optout_sweep.py").read_text()
    tree = ast.parse(src)

    imported = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "gtm_core.reply_classify"
        and any(a.name == "classify_reply" for a in n.names)
        for n in ast.walk(tree)
    )
    assert imported, "agent/optout_sweep.py no longer imports classify_reply"

    called = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "classify_reply"
        for n in ast.walk(tree)
    )
    assert called, (
        "classify_reply is imported into agent/optout_sweep.py but never called — "
        "the producer would report as wired while every reply fell back to the default"
    )


def test_the_classifier_result_reaches_build_signal():
    """The narrow regression this guards: classifying, then passing a constant anyway."""
    tree = ast.parse((REPO / "agent" / "optout_sweep.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_signal"
            and len(node.args) >= 2
        ):
            type_arg = node.args[1]
            assert not isinstance(type_arg, ast.Constant), (
                "build_signal() is called with a literal signal_type in optout_sweep — "
                "every reply would be recorded as the same type again"
            )
            return
    raise AssertionError("no build_signal() call found in agent/optout_sweep.py")
