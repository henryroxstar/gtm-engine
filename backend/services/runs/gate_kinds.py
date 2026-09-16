"""Gate kinds: the rules that name a gate on the wire, shared by the real executors and the
dev-only fake (LD-03).

The kind is what ``run_gates.gate`` stores (V022 CHECK: ``plan | publish | email_enroll |
review``), what ``awaiting_approval.gate`` carries and what push labels, so a client keys its
gate UI on it. Both real executors and the fake executor (``fake.py``) call these same
functions, so a local run cannot report a kind a real run would not. The module is a pure leaf
that imports nothing, so all three can share the one rule with no import cycle, and the rule
cannot grow a side effect (pinned by ``tests/backend/test_fake_runs.py``).
"""

from __future__ import annotations

#: The sentinels a prompt run's stream emits to pause, and the ``pending_gate`` every pack gate
#: records (a legacy constant there).
_GATE_PLAN_SENTINEL = "⟦GATE:plan⟧"
_GATE_PUBLISH_SENTINEL = "⟦GATE:publish⟧"


def prompt_gate_kind(sentinel: str) -> str:
    """A prompt run's gate kind, from the sentinel its stream emitted."""
    return "publish" if "publish" in sentinel else "plan"


def pack_gate_kind(draft_kind: str | None) -> str:
    """A pack gate's kind, from the draft its node left at the gate (``agent.gate_actions``):
    a plan draft → ``plan``, an enroll draft → ``email_enroll``, none → ``review``."""
    return {"plan": "plan", "enroll": "email_enroll"}.get(draft_kind, "review")


def dispatch_target(nodes, gated_node_id: str) -> tuple[str | None, str]:
    """Which declared external effect the approval of ``gated_node_id`` dispatches, and
    on which node: ``(effect, node_id)``, or ``(None, gated_node_id)`` for nothing.

    The gated node's OWN declaration wins. Otherwise a DIRECT successor declaring
    ``email_enroll`` is the target: a dispatch-declared node short-circuits to SKIPPED the
    instant the runner reaches it (agent/pipeline_executor.py), so it can never itself
    pause — the approval of its predecessor (``sequence``, whose enroll-draft it needs) is
    the only point where enrollment can happen. Same shape as the VPS/CLI path's
    ``agent/__main__.py:_dispatch_gate_successors``. ``publish`` successors are
    deliberately NOT resolved here: approving a ``studio`` gate would then post to
    LinkedIn, a separate decision (PENDING.md "Backend pack-mode Gate-2 dispatch").
    """
    own = next((getattr(n, "external_effect", None) for n in nodes if n.id == gated_node_id), None)
    if own is not None:
        return own, gated_node_id
    succ = next(
        (
            n
            for n in nodes
            if gated_node_id in getattr(n, "depends_on", ())
            and getattr(n, "external_effect", None) == "email_enroll"
        ),
        None,
    )
    if succ is not None:
        return "email_enroll", succ.id
    return None, gated_node_id
