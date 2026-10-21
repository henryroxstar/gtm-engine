"""What a fake run plays (``GTM_FAKE_RUNS``; ``fake.py`` is the lifecycle that plays it): the
node ids, where it gates and with which kind, the bytes each gate holds, and the node
``GTM_FAKE_RUN_FAIL_NODE`` fails. It derives these from the resolved variant and that knob,
and writes nothing. Held to the same import scan as ``fake.py``.
"""

from __future__ import annotations

import functools
import json
import logging
import os

from .gate_kinds import _GATE_PLAN_SENTINEL, dispatch_target, pack_gate_kind, prompt_gate_kind

log = logging.getLogger(__name__)

#: The node id to fail when a fake run reaches it. Empty or unset: nothing fails.
FAIL_NODE_ENV = "GTM_FAKE_RUN_FAIL_NODE"
#: The terminal error of the node it names.
FAIL_NODE_ERROR = "fake failure injected at node '{node_id}' by GTM_FAKE_RUN_FAIL_NODE"

# The script for a prompt run, or for a pack request whose variant cannot be resolved.
_FIXED_NODES = ("research", "draft", "deliver")
_FIXED_GATES = {"draft": prompt_gate_kind(_GATE_PLAN_SENTINEL)}
_FIXED_SUCCESSORS: dict[str, str] = {}

#: The first gate's content of record. Fictional, and says what it is on its first line.
FAKE_DRAFT = (
    "FAKE RUN — scripted by GTM_FAKE_RUNS for local development. No model was called and "
    "nothing will be published.\n\n"
    "Example Widgets Ltd just opened its spring catalogue: three new widget sizes, a "
    "recycled-steel line, and same-week shipping.\n\n"
    "If your team has been waiting on the 40 mm hinge, it is in stock today.\n\n"
    "#widgets #manufacturing"
)

#: The first line of every fake gate's ``pending_content``: says what it is, and names the node.
_GATE_HEADER = "FAKE RUN — gate at node '{node_id}': approve, edit or reject the text below."

#: The markdown a node completed inline by an approved fake ``email_enroll`` gate carries, where
#: a real run's block carries the enrollment dispatch's outcome line.
FAKE_ENROLL_OUTCOME = (
    "FAKE RUN — enrollment skipped: GTM_FAKE_RUNS scripted this step, so nobody was enrolled "
    "in any sequence."
)

#: The skill that writes the plan draft a real ``plan`` gate shows (``plans/.pending/``).
_PLAN_DRAFT_SKILL = "content-plan"

#: The people a fake ``email_enroll`` gate names, keyed by sequencer field label as a real
#: enroll-draft's rows are. Fictional.
_FAKE_PROSPECTS = (
    {
        "Email": "pat.example@example.com",
        "First Name": "Pat",
        "Last Name": "Example",
        "Company": "Example Widgets Ltd",
        "Why Now": "Opened a second site this spring",
    },
    {
        "Email": "sam.sample@example.com",
        "First Name": "Sam",
        "Last Name": "Sample",
        "Company": "Sample Hinges Co",
        "Why Now": "Hiring a first operations lead",
    },
)

#: The sequence copy a fake ``email_enroll`` gate approves alongside the people: every step
#: with all of its variants, as a real enroll-draft carries it. Fictional.
_FAKE_STEPS = (
    {
        "step_id": "fake-step-1",
        "variants": [
            {
                "subject": "A question about {{Company}}",
                "content": "<p>Hi {{First Name}}, {{Why Now}}. FAKE RUN copy.</p>",
                "preheader": "",
            }
        ],
    },
    {
        "step_id": "fake-step-2",
        "variants": [{"subject": "", "content": "<p>Following up. FAKE RUN copy.</p>"}],
    },
)


def _gate_frame(node_id: str) -> str:
    return _GATE_HEADER.format(node_id=node_id) + "\n"


def _gate_content(node_id: str, body: str) -> str:
    """The exact bytes the gate at ``node_id`` holds (and a decision's content_sha binds to)."""
    return _gate_frame(node_id) + body


def _enroll_stub(node_id: str) -> str:
    """What a real ``sequence`` node leaves at its gate: an ``import_prospects_to_sequence``
    enroll-draft in the ``agent/gate_actions.py`` shape — the approved ``steps`` beside the
    ``prospect_list``. One line, so its first line still says it is fake."""
    return json.dumps(
        {
            "note": f"FAKE RUN — gate at node '{node_id}': scripted by GTM_FAKE_RUNS; "
            "approving it enrolls nobody.",
            "tool": "import_prospects_to_sequence",
            "sequence_id": "fake-sequence",
            "step_id": "fake-step-1",
            "steps": list(_FAKE_STEPS),
            "prospect_list": list(_FAKE_PROSPECTS),
        },
        ensure_ascii=False,
    )


def _dnc_stub(node_id: str) -> str:
    """What a real ``review`` node leaves at an SC9 DNC gate: a dnc-draft in the
    ``agent/gate_actions.py`` shape — addresses plus the ledger evidence for each.

    The addresses are FICTIONAL and, in a fake run, are also refused at dispatch: the
    shared dispatcher intersects them with the profile's own opt-out ledger, and a fake
    run has none. So approving this gate suppresses nobody twice over — the kill switch is
    closed and the evidence check would refuse it anyway."""
    return json.dumps(
        {
            "note": f"FAKE RUN — gate at node '{node_id}': scripted by GTM_FAKE_RUNS; "
            "approving it suppresses nobody.",
            "addresses": ["dana@acme.example"],
            "evidence": {
                "dana@acme.example": {
                    "event": "optout_detected",
                    "thread_id": "fake-thread-1",
                    "ts": "2026-09-21T10:00:00Z",
                }
            },
        },
        ensure_ascii=False,
    )


def _gate_pending(node_id: str, kind: str, text: str) -> tuple[str, str]:
    """The bytes the gate holds, and the text an approval without an edit carries on."""
    if kind == "email_enroll":
        stub = _enroll_stub(node_id)
        return stub, stub
    if kind == "dnc_add":
        stub = _dnc_stub(node_id)
        return stub, stub
    return _gate_content(node_id, text), text


def _fail_node(nodes: tuple[str, ...]) -> str | None:
    """The node :data:`FAIL_NODE_ENV` names, when this run has it."""
    raw = os.getenv(FAIL_NODE_ENV, "").strip()
    if not raw:
        return None
    if raw not in nodes:
        _warn_unknown_fail_node(raw, nodes)
        return None
    return raw


@functools.cache
def _warn_unknown_fail_node(raw: str, nodes: tuple[str, ...]) -> None:
    """Once per process per (value, script): a typo is visible, and not on every run."""
    log.warning(
        "%s=%r names no node of this fake run (%s) — ignored, the run is not failed",
        FAIL_NODE_ENV,
        raw,
        ", ".join(nodes),
    )


def script_nodes(
    repo_root, workspace_id: str, profile_name: str, pack: str | None, variant: str | None
) -> tuple[tuple[str, ...], dict[str, str], dict[str, str]]:
    """The node ids to script, in order; the gate kind to hold at each gated one; and, per
    gate, the node its approval completes inline.

    A pack request scripts its variant's REAL node ids, so a client's DAG view joins onto the
    same ``PackDescriptor.nodes[].id`` a real run lights up, and gates where a real run would
    (:func:`_gate_kinds`). Anything unresolvable falls back to the fixed script.
    """
    if pack and variant:
        resolved = _pack_script(repo_root, workspace_id, profile_name, pack, variant)
        if resolved is not None:
            return resolved
    return _FIXED_NODES, _FIXED_GATES, _FIXED_SUCCESSORS


def _pack_script(repo_root, workspace_id, profile_name, pack, variant):
    try:
        from gtm_core.paths import workspace_profiles_root

        from ...pack_catalog import resolve_variant

        profiles_root = workspace_profiles_root(workspace_id, repo_root)
        nodes = resolve_variant(repo_root, profiles_root, profile_name, pack, variant).graph.nodes
    except Exception:  # noqa: BLE001 — admission already resolved it; the fixed script is the fallback
        return None
    if not nodes:
        return None
    return tuple(n.id for n in nodes), _gate_kinds(nodes), _successors(nodes)


def _successors(nodes) -> dict[str, str]:
    """Gated node id → the node its approval dispatches (``sequence`` → ``sequence-enroll``),
    which a real run completes inline with that approval rather than ever running it."""
    pairs = ((n.id, dispatch_target(nodes, n.id)[1]) for n in nodes if n.gate)
    return {node_id: target for node_id, target in pairs if target != node_id}


def _gate_kinds(nodes) -> dict[str, str]:
    """Gated node id → the kind a real run reports there, via :mod:`.gate_kinds`.

    A node an approval completes inline is no gate. The draft a real node leaves is the one its
    skill writes: ``content-plan`` a plan draft, and the node whose approval dispatches
    enrollment an enroll draft (the real dispatch fails the run without one).
    """
    inline = set(_successors(nodes).values())
    kinds = {}
    for node in nodes:
        if not node.gate or node.id in inline:
            continue
        effect, _target = dispatch_target(nodes, node.id)
        if effect == "publish":
            # RL-01: a real pack run cannot pause here yet. When it can, the gate is the kind
            # a prompt run's publish gate, run_gates (V022) and push already name — and the
            # only one whose edit ``decisions.refuses_edit`` accepts with no draft to apply.
            kinds[node.id] = "publish"
        elif effect == "email_enroll":
            kinds[node.id] = pack_gate_kind("enroll")
        elif effect == "dnc_add":
            kinds[node.id] = pack_gate_kind("dnc")
        else:
            kinds[node.id] = pack_gate_kind("plan" if node.skill == _PLAN_DRAFT_SKILL else None)
    return kinds


def _step_text(node_id: str) -> str:
    return f"Fake run: {node_id} finished (scripted by GTM_FAKE_RUNS; no model was called)."
