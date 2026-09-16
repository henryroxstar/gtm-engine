"""Six words for where a prospecting-pipeline row stands. One derivation, no I/O.

A leaf module in the same shape as :mod:`gtm_core.lane_verdicts` — no imports beyond
``__future__``, plus the one thing that module didn't need: a function. Everything a
router lane means to the pipeline (``personalised``/``repair``/``generic``/``hold``/
``excluded``, twelve hold triggers, four exclude triggers) is operator-hostile — none of
those words says whose move it is next. This module collapses that vocabulary down to
the six an operator actually needs: is it on me, is it done, is the machine handling it,
is it already loaded somewhere, is it closed, or do we not even have an address for this
person yet.

**Why the twelve hold-trigger ids and four exclude-trigger ids are hand-copied here
rather than imported from** :mod:`gtm_core.lanes.model`. Importing that submodule forces
Python to run ``gtm_core/lanes/__init__.py`` first (a submodule import always runs its
package's ``__init__``), and that ``__init__`` pulls in the router, the hold-decisions
ledger, the hold sheet, and — transitively, through those — ``account_integrity`` and
``prospects_consolidate``. That is the opposite of the leaf shape ``lane_verdicts.py``
sets as the template for this module (see that module's own docstring on why it can't
even import the ``lanes`` package it feeds). So the two trigger lists below are literal
tuples, not an import — and :mod:`tests.test_prospect_status` pins them equal to the live
``HOLD_ORDER``/``EXCLUDE_ORDER`` constants, so a trigger added or renamed in
``lanes/model.py`` fails a test here instead of silently going unrecognised (or worse,
silently mis-mapped) by this module.
"""

from __future__ import annotations

#: Ordered ids an operator's six-way status can take. ``needs_address`` is not derived
#: from a (lane, reason) pair at all — see :func:`needs_address` — but shares this
#: vocabulary because it answers the same question ("where does this row stand").
STATUSES: tuple[str, ...] = (
    "waiting_on_you",
    "ready_to_send",
    "being_fixed",
    "in_sending_tool",
    "not_emailing",
    "needs_address",
)

LABELS: dict[str, str] = {
    "waiting_on_you": "Waiting on you",
    "ready_to_send": "Ready to send",
    "being_fixed": "Being fixed",
    "in_sending_tool": "In the sending tool",
    "not_emailing": "Not emailing",
    "needs_address": "Needs an address",
}

#: Whose move it is next, for each status — the one fact ``lane``/``trigger`` never spell
#: out on their own.
NEXT_STEP: dict[str, str] = {
    "waiting_on_you": "yours — one decision",
    "ready_to_send": "nobody's — it is done",
    "being_fixed": "the machine's — no action",
    "in_sending_tool": "already loaded — do not load again",
    "not_emailing": "closed",
    "needs_address": "accounts in the ledger — the machine's, then yours if it misses",
}


class UnmappedStatus(ValueError):
    """A ``(lane, reason)`` pair — or a hold/exclude reason inside one — this module has
    never seen. Raised rather than swallowed: a lane or trigger this module doesn't
    recognise is a real gap in the mapping, not a row to silently drop."""


#: Hold triggers (``gtm_core.lanes.model.HOLD_ORDER``), copied literally — see the module
#: docstring for why. ``researcher-drop`` is the one hold trigger that does NOT mean
#: "waiting on you" (see :func:`status_of`).
_HOLD_TRIGGERS: frozenset[str] = frozenset(
    {
        "competitor-adjacent",
        "partner",
        "regulator",
        "prior-contact",
        "negative-reply",
        "engaged-account",
        "strategic-account",
        "judge-account-scope",
        "researcher-drop",
        "untraceable-number",
        "tier-a-generic",
        "duplicate-contact",
    }
)

#: Exclude triggers (``gtm_core.lanes.model.EXCLUDE_ORDER``), copied literally — see the
#: module docstring for why.
_EXCLUDE_TRIGGERS: frozenset[str] = frozenset(
    {"suppressed", "optout", "already-enrolled", "competitor-direct"}
)

#: Exclude reasons that mean the row is closed rather than loaded somewhere.
_EXCLUDE_NOT_EMAILING: frozenset[str] = frozenset({"suppressed", "optout", "competitor-direct"})


def status_of(lane: str, reason: str) -> str:
    """The six-way status a routed row earns, from its ``lane`` and its ``reason``.

    ``reason`` is ``gtm_core.lanes.model.Routed.stable_reason`` as written to
    ``lanes-state.jsonl``'s ``"reason"`` field (falling back to the older ``"trigger"``
    field is the CALLER's job — see ``gtm_core.prospect_status_cli`` — because only the
    caller knows which field a given record actually carries).

    Decided BY LANE first; ``reason`` is read only where the lane alone is ambiguous
    (``hold``/``excluded``):

    * ``personalised``/``generic`` → ``ready_to_send``, whatever ``reason`` says — both are
      loadable, sendable lanes.
    * ``repair`` → ``being_fixed``, whatever ``reason`` says — the machine is re-working it.
    * ``hold`` → ``not_emailing`` if the trigger is ``researcher-drop`` (the research verdict
      itself said drop), else ``waiting_on_you`` for any other of the twelve known hold
      triggers — INCLUDING ``duplicate-contact``: a second contact at the account being held
      does not mean a colleague is loaded into the sending tool, only that someone at that
      account is in some lane this wave, which is exactly the operator's call to make. An
      unrecognised trigger raises.
    * ``excluded`` → ``in_sending_tool`` for ``already-enrolled`` (the double-enrolment
      guard — this row already has a destination); ``not_emailing`` for ``suppressed``,
      ``optout``, or ``competitor-direct``; ``not_emailing`` for a ``suppress:<trigger>``
      reason (an excluded row that reached ``excluded`` via an operator's ``suppress``
      decision on one of the twelve hold triggers — see ``gtm_core.lanes.router.
      _apply_decision`` and ``Routed.stable_reason``, which is the ONLY decided-row shape
      that lands in the ``excluded`` lane: a ``generic``/``salvage`` decision routes to
      ``generic``/``repair`` instead, never here). Anything else raises.
    * blank, or any lane outside the five known lanes → raises.
    """
    lane = (lane or "").strip()
    reason = (reason or "").strip()

    if lane in ("personalised", "generic"):
        return "ready_to_send"
    if lane == "repair":
        return "being_fixed"
    if lane == "hold":
        trigger = reason.rsplit(":", 1)[-1]
        if trigger == "researcher-drop":
            return "not_emailing"
        if trigger in _HOLD_TRIGGERS:
            return "waiting_on_you"
        raise UnmappedStatus(f"hold trigger {reason!r} is not one of the known hold triggers")
    if lane == "excluded":
        if reason == "already-enrolled":
            return "in_sending_tool"
        if reason in _EXCLUDE_NOT_EMAILING:
            return "not_emailing"
        if reason.startswith("suppress:") and reason.split(":", 1)[1] in _HOLD_TRIGGERS:
            return "not_emailing"
        raise UnmappedStatus(f"excluded reason {reason!r} is not recognised")
    raise UnmappedStatus(f"lane {lane!r} is not one of the five known lanes")


#: Ledger statuses (``gtm_core.prospects_state.RETIRED_STATUSES``) a "needs an address"
#: count must exclude — an account already retired from the pipeline doesn't need one.
#: Hand-copied rather than imported, to keep this module leaf (no imports beyond
#: ``__future__`` — see the module docstring); :mod:`tests.test_prospect_status` pins it
#: equal to the live ``RETIRED_STATUSES`` constant, the same tripwire-against-drift
#: pattern as ``_HOLD_TRIGGERS``/``_EXCLUDE_TRIGGERS`` above.
_RETIRED_LEDGER_STATUSES: frozenset[str] = frozenset(
    {"disqualified", "do-not-contact", "closed-lost"}
)


#: Stringified null/placeholder tokens from dirty CRM/export data that should not
#: evaluate as real values for contact names or emails.
_PSEUDO_VALUES: frozenset[str] = frozenset({"", "none", "null", "n/a", "undefined", "unknown"})


def needs_address(item: dict) -> bool:
    """True if this ``latest.json`` item is a named contact we cannot reach.

    Named (``contact_name`` truthy and not a pseudo-value) AND no reachable address
    (``contact_email`` falsy or a pseudo-value) AND not already retired from the pipeline
    (``status`` not in disqualified/do-not-contact/closed-lost).
    """
    name = str(item.get("contact_name") or "").strip()
    email = str(item.get("contact_email") or "").strip()
    status = str(item.get("status") or "").strip()

    has_name = bool(name) and name.lower() not in _PSEUDO_VALUES
    has_email = bool(email) and email.lower() not in _PSEUDO_VALUES
    return has_name and not has_email and status not in _RETIRED_LEDGER_STATUSES
