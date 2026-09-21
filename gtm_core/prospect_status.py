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

from dataclasses import asdict, dataclass

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
        "unattended-generic",
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
_PSEUDO_VALUES: frozenset[str] = frozenset(
    {"", "none", "null", "n/a", "undefined", "unknown", "unverified"}
)


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
    has_email = bool(email) and email.lower() not in _PSEUDO_VALUES and "@" in email
    return has_name and not has_email and status not in _RETIRED_LEDGER_STATUSES


@dataclass
class AttritionReceipt:
    """Funnel conservation receipt across intake, filtering stages, hold, and ready."""

    total_intake: int
    failed_fit: int
    failed_intent: int
    failed_enrichment: int
    held: int
    ready: int

    @property
    def rejected(self) -> int:
        return self.failed_fit + self.failed_intent + self.failed_enrichment

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def verify_funnel_conservation(receipt: AttritionReceipt | dict[str, int]) -> bool:
    """Verify strict conservation of accounts.

    Total Intake == (Failed Fit + Failed Intent + Failed Enrichment + Held + Ready).
    Raises ValueError if conservation is violated.
    """
    if isinstance(receipt, dict):
        total = receipt.get("total_intake", 0)
        failed_fit = receipt.get("failed_fit", 0)
        failed_intent = receipt.get("failed_intent", 0)
        failed_enrichment = receipt.get("failed_enrichment", 0)
        held = receipt.get("held", 0)
        ready = receipt.get("ready", 0)
    else:
        total = receipt.total_intake
        failed_fit = receipt.failed_fit
        failed_intent = receipt.failed_intent
        failed_enrichment = receipt.failed_enrichment
        held = receipt.held
        ready = receipt.ready

    expected_sum = failed_fit + failed_intent + failed_enrichment + held + ready
    if total != expected_sum:
        raise ValueError(
            f"Funnel conservation violated: Total Intake ({total}) != "
            f"Failed Fit ({failed_fit}) + Failed Intent ({failed_intent}) + "
            f"Failed Enrichment ({failed_enrichment}) + Held ({held}) + Ready ({ready}) "
            f"[Sum = {expected_sum}]"
        )
    return True


def _canonical_account_key(item: dict) -> str:
    import re

    from .slugify import slug

    company = str(
        item.get("company")
        or item.get("business_name")
        or item.get("company_name")
        or item.get("account")
        or item.get("account_slug")
        or item.get("slug")
        or ""
    ).strip()
    if company:
        return slug(company)
    domain = str(item.get("domain") or item.get("company_domain") or "").strip().lower()
    if domain:
        clean_domain = re.sub(r"^https?://", "", domain).split("/")[0].removeprefix("www.")
        return f"d:{clean_domain}"
    cid = str(item.get("id") or item.get("account_id") or "").strip().lower()
    if cid:
        return f"id:{cid}"
    return ""


def _classify_account_stage(item: dict) -> str:
    stage = str(item.get("stage") or "").strip().lower()
    if stage in (
        "failed_fit",
        "failed_intent",
        "failed_enrichment",
        "enrichment_miss",
        "held",
        "ready",
    ):
        return "failed_enrichment" if stage == "enrichment_miss" else stage

    status = str(item.get("status") or "").strip().lower()
    lane = str(item.get("lane") or "").strip().lower()
    verdict = str(item.get("verdict") or "").strip().lower()
    relation = str(item.get("category_relation") or "").strip().lower()
    tier = str(item.get("tier") or "").strip().upper()
    reason = (
        str(item.get("reason") or item.get("lane_reason") or item.get("verdict_reason") or "")
        .strip()
        .lower()
    )

    # 1. Fit / Disqualification Gate
    if (
        verdict == "drop"
        or relation in ("competitor", "regulator")
        or status
        in ("disqualified", "off-icp", "failed_fit", "closed", "closed-lost", "do-not-contact")
        or tier in ("C", "DROP")
        or item.get("fit") is False
        or (lane == "excluded" and reason != "already-enrolled")
    ):
        return "failed_fit"

    # 2. Intent / Why-Now Signal Gate
    if (
        verdict == "re-angle"
        or status in ("no-intent", "failed_intent")
        or item.get("intent") is False
    ):
        return "failed_intent"

    # 3. Held / Review Gate
    if lane in ("hold", "repair") or status in (
        "held",
        "waiting_on_you",
        "review",
        "being_fixed",
        "new",
    ):
        return "held"

    # 4. Enrichment / Contact Gate
    raw_email = str(item.get("contact_email") or item.get("email") or "").strip().lower()
    has_email = bool(raw_email) and raw_email not in _PSEUDO_VALUES and "@" in raw_email
    email_status = str(item.get("email_status") or "").strip().lower()
    if (
        not has_email
        or email_status == "unverified"
        or status
        in ("contact-defective", "no-contact", "unverified", "failed_enrichment", "enrichment_miss")
        or reason in ("needs-verification", "unverified")
    ):
        return "failed_enrichment"

    # 5. Ready
    if lane in ("personalised", "generic") or status in (
        "ready",
        "ready_to_send",
        "contact-resolved",
        "active",
        "in_sending_tool",
    ):
        return "ready"

    return "ready" if has_email else "failed_enrichment"


def compute_attrition_receipt(accounts: list[dict]) -> AttritionReceipt:
    """Compute deduplicated funnel attrition receipt across accounts."""
    from collections import Counter

    deduped: dict[str, dict] = {}
    for item in accounts:
        key = _canonical_account_key(item)
        if not key:
            key = f"anon:{id(item)}"
        if key in deduped:
            merged = dict(deduped[key])
            merged.update(item)
            deduped[key] = merged
        else:
            deduped[key] = dict(item)

    counts = Counter(_classify_account_stage(acct) for acct in deduped.values())
    receipt = AttritionReceipt(
        total_intake=len(deduped),
        failed_fit=counts["failed_fit"],
        failed_intent=counts["failed_intent"],
        failed_enrichment=counts["failed_enrichment"],
        held=counts["held"],
        ready=counts["ready"],
    )
    verify_funnel_conservation(receipt)
    return receipt


def format_attrition_receipt(receipt: AttritionReceipt) -> str:
    """Format Attrition Receipt as a clean CLI waterfall."""
    waterfall = (
        f"[Total Intake: {receipt.total_intake}] → "
        f"[Failed Fit: {receipt.failed_fit}] → "
        f"[Failed Intent: {receipt.failed_intent}] → "
        f"[Enrichment Miss: {receipt.failed_enrichment}] → "
        f"[Held: {receipt.held}] → "
        f"[Ready: {receipt.ready}]"
    )
    lines = [
        "Attrition Receipt:",
        f"  [Total Intake]    {receipt.total_intake:>5}",
        f"  [Failed Fit]      {receipt.failed_fit:>5}",
        f"  [Failed Intent]   {receipt.failed_intent:>5}",
        f"  [Enrichment Miss] {receipt.failed_enrichment:>5}",
        f"  [Held]            {receipt.held:>5}",
        f"  [Ready]           {receipt.ready:>5}",
        f"Waterfall: {waterfall}",
    ]
    return "\n".join(lines)
