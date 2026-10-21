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
DECISIONS = ("suppress", "generic", "salvage", "send")

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
    "champion-missing",
    "missing-hook-cell",
    "unattended-generic",
    "unattended-repair",
)

#: The hold trigger an UNATTENDED route writes instead of each send-path lane it refuses to
#: fill without a person present (fail-closed). This mapping is the only spelling: the router
#: reads it rather than composing ``f"unattended-{lane}"`` — which is how ``unattended-repair``
#: once reached a state file that nothing downstream had ever heard of, and ended the
#: mandatory status step in a traceback. Every value is also a ``HOLD_ORDER`` member with a
#: question and copy below (pinned by ``tests/unit/test_lanes_route_state.py``).
UNATTENDED_TRIGGERS: dict[str, str] = {
    "generic": "unattended-generic",
    "repair": "unattended-repair",
}

#: Triggers that EXCLUDE a row outright (deterministic, no decision to make). ``already-
#: enrolled`` is the double-enrolment guard: a row already in a registered sequence list has
#: a destination and must not be routed into a second one.
EXCLUDE_ORDER = ("suppressed", "optout", "already-enrolled", "competitor-direct")

#: Protective hold triggers that protect sensitive accounts/people (prior contact,
#: negative reply, active conversation, regulator, competitor). These may never be
#: automated via [auto] policy rules (PS-R I5) and may not be overwritten by duplicate-contact (PS-R I6).
PROTECTIVE_HOLD_TRIGGERS: frozenset[str] = frozenset(
    {
        "competitor-adjacent",
        "regulator",
        "prior-contact",
        "negative-reply",
        "engaged-account",
        "duplicate-contact",
        "champion-missing",
        "missing-hook-cell",
    }
)

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
    "unattended-generic": (
        "Unattended run fail-closed",
        {
            "suppress": "suppress",
            "generic": "generic",
            "salvage": "salvage",
        },
    ),
    "unattended-repair": (
        "Unattended run stopped on an email the review wants reworked",
        {
            "suppress": "keep this account out of outreach (reversible)",
            "generic": "send the seat email instead of a reworked one",
            "salvage": "rework it — say the better fact, argument, or person",
        },
    ),
    "champion-missing": (
        "This account has no champion in the wedge seats",
        {
            "send": "send anyway",
            "salvage": "find a champion",
            "suppress": "skip this contact",
        },
    ),
    "missing-hook-cell": (
        "Missing or unresolvable messaging matrix hook cell",
        {
            "suppress": "skip this contact",
            "generic": "send generic email",
            "salvage": "add missing hook cell to hook-matrix.md",
        },
    ),
}

#: Which QUESTION a hold trigger answers, for the hold sheet (PS12). Several triggers with
#: near-identical suppress/generic/salvage meanings ask the operator the SAME question, so
#: the sheet groups by question rather than by the trigger id that happened to fire —
#: ``account-off-limits`` covers four different list matches that all reduce to "should this
#: account get automated outreach at all?". Every ``HOLD_ORDER`` trigger maps to exactly one
#: question; a trigger with no natural sibling maps to its own question.
HOLD_QUESTION: dict[str, str] = {
    "competitor-adjacent": "account-off-limits",
    "partner": "account-off-limits",
    "regulator": "account-off-limits",
    "strategic-account": "account-off-limits",
    "prior-contact": "already-in-conversation",
    "negative-reply": "already-in-conversation",
    "engaged-account": "already-in-conversation",
    "judge-account-scope": "verdict-said-no",
    "researcher-drop": "verdict-said-no",
    "untraceable-number": "number-not-grounded",
    "tier-a-generic": "tier-a-would-get-generic",
    "duplicate-contact": "second-contact-same-account",
    "unattended-generic": "unattended-fail-closed",
    "unattended-repair": "unattended-needs-rework",
    "champion-missing": "champion-missing",
    "missing-hook-cell": "missing-hook-cell",
}

#: Plain-English title + per-choice meaning per QUESTION id (not per trigger) — adapted from
#: :data:`HOLD_COPY`, merging the triggers :data:`HOLD_QUESTION` groups together into one
#: shared meaning so the sheet asks the question once per group instead of once per trigger.
#: The questions whose "suppress" retires the whole ACCOUNT, as the sheet's own copy for them
#: says ("keep it out of automated outreach", "honour it", "agree with the recommendation").
#: Every other question's suppress drops only the one PERSON the row names — the copy for a
#: second contact reads "one person per account — drop this one", and for an ungrounded number
#: "do not lose the account". Until 2026-09-24 every suppress retired the account, so dropping
#: a duplicate contact silently removed the colleague who was staying on the list.
#: A closed set: a question not named here drops only the person, which still protects them
#: and retires nobody else.
ACCOUNT_SCOPED_SUPPRESS: frozenset[str] = frozenset(
    {"account-off-limits", "already-in-conversation", "verdict-said-no"}
)

QUESTION_COPY: dict[str, tuple[str, dict[str, str]]] = {
    "account-off-limits": (
        "This account is on an off-limits list (competitor, partner, regulator, or strategic)",
        {
            "suppress": "keep it out of automated outreach (reversible)",
            "generic": "send the seat email anyway",
            "salvage": "re-aim it — partnership motion, hand-written 1:1, or a different "
            "angle — say which in the note",
        },
    ),
    "already-in-conversation": (
        "There is already a relationship signal here — a prior email, a 'no', or an open "
        "conversation",
        {
            "suppress": "honour it — keep this address/account out of cold outreach (reversible)",
            "generic": "send the seat email anyway (not recommended after a negative reply)",
            "salvage": "follow up in context instead — say what changed, or who owns it",
        },
    ),
    "verdict-said-no": (
        "The review or the researcher already advised against emailing this row",
        {
            "suppress": "agree with the recommendation (reversible)",
            "generic": "disagree — send the seat email",
            "salvage": "re-research or re-argue — say the better fact, argument, or person",
        },
    ),
    "number-not-grounded": (
        "The email cites a number no case study backs",
        {
            "suppress": "not applicable — fix the copy, do not lose the account",
            "generic": "send the seat email without the number",
            "salvage": "fix the number in the spec, then re-check — say the source",
        },
    ),
    "tier-a-would-get-generic": (
        "A Tier-A account would get the standard email",
        {
            "suppress": "keep it out of the standard sequence (reversible)",
            "generic": "the seat email is fine for this account",
            "salvage": "write a 1:1 instead, or name a better fact",
        },
    ),
    "second-contact-same-account": (
        "A second person at this account is already in this wave",
        {
            "suppress": "one person per account — drop this one (reversible)",
            "generic": "email both (not recommended)",
            "salvage": "swap: this person, not the first — say why",
        },
    ),
    "unattended-fail-closed": (
        "Unattended run encountered an account lacking a strong story",
        {
            "suppress": "keep it out of outreach (reversible)",
            "generic": "send the seat email anyway",
            "salvage": "re-research or write a custom outreach note",
        },
    ),
    "unattended-needs-rework": (
        "Unattended run stopped on an email the review wants reworked",
        {
            "suppress": "keep it out of outreach (reversible)",
            "generic": "send the seat email instead of a reworked one",
            "salvage": "rework it — say the better fact, argument, or person",
        },
    ),
    "champion-missing": (
        "This account has no champion in the wedge seats",
        {
            "send": "send anyway",
            "salvage": "find a champion",
            "suppress": "skip this contact",
        },
    ),
    "missing-hook-cell": (
        "Missing or unresolvable messaging matrix hook cell",
        {
            "suppress": "skip this contact",
            "generic": "send generic email",
            "salvage": "add missing hook cell to hook-matrix.md",
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
    judge_calibrated: str = ""  # "true" / "false" / "" when the record predates the flag
    grounding: str = ""
    body_hash: str = ""
    source: str = ""  # which records file the judge verdict came from
    decided: str = ""  # a prior decision or policy that was applied, if any
    flags: list[str] = field(default_factory=list)  # "ambiguous-judge", "contested", ...
    #: A stable code for WHICH VERDICT BRANCH decided this row's lane, filled only when the
    #: row never fired a hold/exclude trigger — see ``stable_reason`` below, which is what
    #: everything downstream should read. Never read this field directly.
    reason_code: str = ""

    @property
    def email(self) -> str:
        return (self.row.get("email") or "").strip().lower()

    @property
    def stable_reason(self) -> str:
        """A short, stable reason code for this row — PS5.

        Every routed row earns one: the ``trigger`` when a hold/exclude trigger fired (a
        decided row stamps ``<choice>:<trigger>`` so a policy/ledger answer is visibly
        distinct from a fresh hold), else the verdict-branch code ``router._verdict_lane``
        (or the stickiness override) assigned. Unlike ``.reason`` below — the composed
        ``lane_reason`` display string — this is meant to be joined on and compared, so it
        never carries the free-text ``detail``.
        """
        if self.decided:
            parts = self.decided.split(":", 2)
            if len(parts) == 3 and parts[1] and self.trigger:
                return f"{parts[1]}:{self.trigger}"
        return self.trigger or self.reason_code

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
