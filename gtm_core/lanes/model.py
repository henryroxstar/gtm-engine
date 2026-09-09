"""The vocabulary of the lane router — lanes, hold triggers, decisions, and what each means.

Kept in one small module so the router, the hold sheet, the decisions ledger, and the tests
all read the same words. Nothing here touches disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Which verdicts may enrol in each lane. Re-exported so the router's vocabulary reads
# complete from here, but DEFINED in `gtm_core.lane_verdicts` — a leaf module — so callers
# that cannot import this package (its `__init__` closes a cycle through
# `account_integrity`) can still reach the rule. See that module for why the second caller
# matters.
from ..lane_verdicts import LANE_VERDICTS  # noqa: F401

#: Every pooled row ends in exactly one of these. Order is the order ``write_lanes`` reports.
LANES = ("personalised", "repair", "generic", "hold", "excluded")

#: Columns the router appends to every lane CSV.
LANE_COLUMNS = ("lane", "lane_reason", "judge_defect_class")

#: What the operator may decide for a held row. Blank means "still deciding" — the row stays
#: held; it is never sent and never suppressed by silence.
DECISIONS = ("suppress", "generic", "salvage")

#: The structured half of a ``salvage`` note. A chip the repair lane can act on without
#: reading prose. ``different-argument:<capability>``, ``different-person:<role>`` and
#: ``revisit:<date>`` carry a value after the colon.
SALVAGE_KINDS = (
    "different-fact",
    "different-argument",
    "different-person",
    "tier-a-1to1",
    "revisit",
)

#: Hold triggers, in RISK order — the order the hold sheet lists its groups. The first
#: trigger that fires names the row's reason; the rest are still evaluated for the record.
HOLD_ORDER = (
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
)

#: Triggers that EXCLUDE a row outright (deterministic, no decision to make). ``already-
#: enrolled`` is the double-enrolment guard: a row already in a registered sequence list has
#: a destination and must not be routed into a second one.
EXCLUDE_ORDER = ("suppressed", "optout", "already-enrolled", "competitor-direct")

#: Plain-English title per hold trigger, plus what each of the three choices does FOR THAT
#: REASON. The meaning shifts by reason, so the sheet spells it out every time rather than
#: once at the top.
HOLD_COPY: dict[str, tuple[str, dict[str, str]]] = {
    "competitor-adjacent": (
        "On the competitor watchlist (adjacent or channel tier)",
        {
            "suppress": "never email this account (reversible)",
            "generic": "send the seat email anyway",
            "salvage": "re-aim: partnership or channel motion, not a sale — say which in the note",
        },
    ),
    "partner": (
        "Recorded as a partner, not a prospect",
        {
            "suppress": "keep them out of cold outreach (reversible)",
            "generic": "send the seat email anyway",
            "salvage": "route to the partner motion — name the person or angle in the note",
        },
    ),
    "regulator": (
        "Regulator, public sector, or a regulated-domain match",
        {
            "suppress": "never cold-email this account (reversible)",
            "generic": "send the seat email anyway",
            "salvage": "hand-write a 1:1 instead — say what it should lead on",
        },
    ),
    "prior-contact": (
        "We already emailed this person",
        {
            "suppress": "never email again (reversible)",
            "generic": "send the seat email anyway (a second first-touch)",
            "salvage": "follow up in the SAME thread instead — say what changed",
        },
    ),
    "negative-reply": (
        "They replied 'no' or asked not to be contacted",
        {
            "suppress": "honour it (recommended; reversible only by you)",
            "generic": "send the seat email anyway — not recommended",
            "salvage": "only if the 'no' was to a different offer — say which",
        },
    ),
    "engaged-account": (
        "Account already in conversation",
        {
            "suppress": "keep cold outreach off this account (reversible)",
            "generic": "send the seat email anyway",
            "salvage": "route to whoever owns the conversation — name them",
        },
    ),
    "strategic-account": (
        "On your strategic-accounts list",
        {
            "suppress": "keep it out of automated outreach (reversible)",
            "generic": "send the seat email anyway",
            "salvage": "hand-write a 1:1 — say the angle",
        },
    ),
    "judge-account-scope": (
        "Judge: this company sells this rather than buys it",
        {
            "suppress": "agree — not a buyer (reversible)",
            "generic": "disagree — send the seat email",
            "salvage": "they are a buyer for a different reason — say which argument fits",
        },
    ),
    "researcher-drop": (
        "Research verdict was 'drop'",
        {
            "suppress": "agree with the researcher (reversible)",
            "generic": "send the seat email anyway",
            "salvage": "re-research: a better fact, argument, or person — say which",
        },
    ),
    "untraceable-number": (
        "The email carries a number no case study backs",
        {
            "suppress": "not applicable — fix the copy, do not lose the account",
            "generic": "send the seat email without the number",
            "salvage": "fix the number in the spec, then re-judge — say the source",
        },
    ),
    "tier-a-generic": (
        "Tier-A account would get the generic email",
        {
            "suppress": "keep it out of the generic sequence (reversible)",
            "generic": "the seat email is fine for this account",
            "salvage": "write a 1:1 instead (tier-a-1to1), or name a better fact",
        },
    ),
    "duplicate-contact": (
        "A second person at an account already in this wave",
        {
            "suppress": "one person per account — drop this one (reversible)",
            "generic": "email both (not recommended)",
            "salvage": "swap: this person, not the first — say why",
        },
    ),
}


@dataclass
class Routed:
    """One pooled row, routed."""

    row: dict
    lane: str
    trigger: str = ""  # the trigger id that decided the lane (hold/excluded), else ""
    detail: str = ""  # the specific evidence for that trigger, human-readable
    judge_verdict: str = ""
    judge_defect_class: str = ""
    judge_scope: str = ""
    judge_note: str = ""
    grounding: str = ""
    body_hash: str = ""
    source: str = ""  # which records file the judge verdict came from
    decided: str = ""  # a prior decision or policy that was applied, if any
    flags: list[str] = field(default_factory=list)  # "ambiguous-judge", "contested", ...

    @property
    def email(self) -> str:
        return (self.row.get("email") or "").strip().lower()

    @property
    def reason(self) -> str:
        """The ``lane_reason`` column: ``<lane>:<trigger> — <detail>`` plus any flags."""
        head = f"{self.lane}:{self.trigger}" if self.trigger else self.lane
        parts = [head]
        if self.detail:
            parts.append(self.detail)
        if self.decided:
            parts.append(f"[{self.decided}]")
        if self.flags:
            parts.append("[" + ",".join(self.flags) + "]")
        return " — ".join(parts[:2]) + (" " + " ".join(parts[2:]) if len(parts) > 2 else "")
