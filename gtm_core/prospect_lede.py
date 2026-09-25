"""The operator's words for where the send list stands — the one renderer of the lede.

PS15, the prospecting status lede. :func:`compose_lede` returns the
lines ``prospects status`` opens with AND the lines the dashboard's status tab opens with:
both surfaces print what this returns, and neither words the same fact its own way. It is
pure — every value is passed in and nothing is opened — so the two cannot drift through a
second read of a file either.

The numbers come from :mod:`gtm_core.prospect_readiness` (computed by the check report,
loaded with a staleness test). Everything here is plain words, scanned by
``tests/lint/operator_vocabulary.py``: no pipeline vocabulary, no ids, no paths but the one
review sheet that exists on disk.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence

from .prospect_readiness import (
    ACCOUNT_STATUS,
    FATES,
    LANE_STATE,
    RECORD_COLUMNS,
    Readiness,
)

#: How each fate reads in the lede — plain words only (``tests/lint/operator_vocabulary.py``).
FATE_WORDS: dict[str, str] = {
    "refused": "held back by the checks",
    "not_scored": "not yet scored",
    "not_admitted": "scored for a different kind of email",
    "judge_dropped": "removed by the email judge",
    "set_aside": "waiting on you or set aside",
    "suppressed": "asked not to be contacted",
    "not_sorted": "not yet sorted",
}

#: The batches the gate admits into (``lane_verdicts.LANE_VERDICTS`` keys, blank excluded)
#: and what each is called on an operator surface. A batch word, never the pipeline word.
BATCH_WORDS: dict[str, str] = {
    "personalised": "personalised",
    "signal": "personalised",
    "generic": "general",
    "repair": "re-draft",
}

#: ``(what it is, what unblocks it)`` per refusal class, in the operator's words. The first
#: half follows a count — "3 accounts with no research file" — so it is written to read for one
#: or many alike: a phrase, never a verb that has to agree with the number.
REFUSAL_COPY: dict[str, tuple[str, str]] = {
    ACCOUNT_STATUS: (
        "at companies that are closed to sending",
        "the next list build removes them by itself",
    ),
    LANE_STATE: ("out of step with how the list was last sorted", "sort the list again"),
    RECORD_COLUMNS: (
        "from a list built before research records existed",
        "re-run the research step for these accounts",
    ),
    "no-dossier": ("with no research file for the company", "run the account research"),
    "why-now-not-a-signal": (
        "whose reason to write is not a dated event",
        "find a dated reason, or send them the general email",
    ),
    "stale-artifact-string": ("with leftover placeholder text", "re-draft those emails"),
    "leadership-freshness": (
        "naming a leader not confirmed as still in the role",
        "re-check who holds the role",
    ),
    "suppressed": ("who asked not to be contacted", "take them off the list"),
    "signal-stale": ("whose reason to write is too old", "find a newer reason"),
    "signal-observed-future": (
        "whose reason to write is dated in the future",
        "re-check the research",
    ),
    "verdict-inadmissible": (
        "scored for a different kind of email than this batch sends",
        "sort the list again",
    ),
    "domain-academic": ("at a university", "decide whether to keep them"),
    "domain-academic-medical": ("at a university hospital", "decide whether to keep them"),
    "academic-medical": ("at a university hospital", "decide whether to keep them"),
}

#: Rule families, matched by prefix when no exact entry exists. Order matters only where
#: one prefix contains another; none do today.
REFUSAL_FAMILIES: tuple[tuple[str, tuple[str, str]], ...] = (
    (
        "signal-",
        ("whose reason to write the research does not back up", "re-check the research"),
    ),
    ("verdict-", ("not yet scored", "run the email quality check")),
    (
        "relation-",
        (
            "at a company whose relationship to us is not settled",
            "decide how to treat those accounts",
        ),
    ),
    (
        "agent-kind-",
        (
            "describing the company's AI agents in a way the research does not support",
            "re-check the research",
        ),
    ),
    ("competitor-", ("at a competitor", "take them off the list")),
    (
        "domain-",
        ("with an email address that does not match the company", "find the right address"),
    ),
    (
        "email-",
        ("with an email address that does not match the company", "find the right address"),
    ),
)

#: What an unknown class reads as. Never the id itself, never an empty slot.
UNKNOWN_REFUSAL: tuple[str, str] = (
    "refused for a reason this summary cannot name",
    "open the check report for the detail",
)

#: The warning pile is not a finding about rows; it has its own sentence.
WARNING_PILE = (
    "more than a person can review",
    "fix the most common warning, or accept it for this run",
)

#: How many reason lines one refused batch shows before pointing at the report.
_MAX_REASONS = 3


def refusal_copy(rule: str) -> tuple[str, str]:
    """The operator sentence for a refusal class — exact, then family, then the fallback."""
    if rule in REFUSAL_COPY:
        return REFUSAL_COPY[rule]
    for prefix, copy in REFUSAL_FAMILIES:
        if rule.startswith(prefix):
            return copy
    return UNKNOWN_REFUSAL


# --- compose (the one renderer of the lede's words) ------------------------------------

#: The go-live words the dashboard observes (``email_campaign_dashboard.model``). The
#: terminal passes none: it cannot see the sending tool, so it never says paused or live.
GO_LIVE_WORDS: dict[str, str] = {
    "none": "nothing loaded yet",
    "staged": "loaded, not started",
    "paused": "paused",
    "active": "live, sending",
    "started": "started, people have been contacted",
    "unknown": "unknown, the sending tool's figures couldn't be read",
}

#: Snapshot statuses meaning a sequence is sending now — one set for every caller (PS20).
LIVE_STATUSES = frozenset({"active", "running", "live"})


def go_live(statuses, contacted, on_record: bool, *, readable: bool = True) -> str:
    """PS20 P1.10 — the ONLY liveness rule. ``statuses`` are the SNAPSHOT's per-row values
    (``live.status``), never the ledger's staged-event status ("paused" by construction).
    ``contacted`` must be a real int > 0 to count as evidence."""
    if not readable:
        return "unknown"
    seen = {str(s or "").strip().lower() for s in statuses}
    if seen & LIVE_STATUSES:
        return "active"
    if "paused" in seen:
        return "paused"
    if isinstance(contacted, int) and not isinstance(contacted, bool) and contacted > 0:
        return "started"
    return "staged" if on_record else "none"


LEDE_TAIL = "For the record"


def _count(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _when(ran_at: str) -> str:
    """``2026-09-24T10:31:07+00:00`` -> ``2026-09-24 10:31 UTC``; anything else verbatim-safe."""
    try:
        t = datetime.datetime.fromisoformat(ran_at)
    except ValueError:
        return "at an unknown time"
    return t.astimezone(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")


def _reason_lines(classes: Sequence[tuple[str, int, str]]) -> list[str]:
    """One line per distinct sentence, largest first. Classes that share a sentence (the
    signal family has a dozen ids) are merged; their counts may overlap — one row can carry
    two findings — so a merged count is stated as "at least" the largest, never summed."""
    groups: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for rule, count, unit in classes:
        copy = WARNING_PILE if unit == "warning" else refusal_copy(rule)
        groups.setdefault(copy, []).append((count, unit))
    out = []
    for (what, unlock), hits in list(groups.items())[:_MAX_REASONS]:
        count, unit = max(hits)
        noun = _count(count, unit, unit + "s")
        prefix = "at least " if len(hits) > 1 else ""
        joiner = ", " if unit == "warning" else " "
        out.append(f"    {prefix}{noun}{joiner}{what}. To fix: {unlock}.")
    if not classes:
        what, unlock = UNKNOWN_REFUSAL
        out.append(f"    rows {what}. To fix: {unlock}.")
    elif len(groups) > _MAX_REASONS:
        out.append(
            f"    and {len(groups) - _MAX_REASONS} more — the check report has the full list."
        )
    return out


def _today_lines(r: Readiness) -> list[str]:
    if r.state == "none":
        return [
            "Today: unknown — the checks have not run on this list yet. Run them before "
            "loading anything."
        ]
    if r.state == "unreadable":
        return [
            "Today: unknown — the last check report could not be read. Run the checks "
            "again before loading anything."
        ]
    if r.state == "stale":
        return [
            "Today: unknown — the list has changed since the checks last ran. Run them "
            "again before loading anything."
        ]
    admitted = r.fates.get("admitted", 0)
    head = (
        f"Today: {admitted:,} of the {_count(r.rows, 'person', 'people')} on the list can go out."
        if admitted
        else f"Today: none of the {_count(r.rows, 'person', 'people')} on the list can go out yet."
    )
    rest = [f"{r.fates[f]:,} {FATE_WORDS[f]}" for f in FATES[1:] if r.fates.get(f)]
    lead = " The rest: " if admitted else " Of them: "
    lines = [head + (f"{lead}{', '.join(rest)}." if rest else "")]
    for lane, refused, classes in r.refusals:
        batch = BATCH_WORDS.get(lane, "")
        which = f"the {batch} batch" if batch else "a batch"
        verb = "is" if refused == 1 else "are"
        lines.append(f"  Why {refused:,} {verb} held back — the checks refused {which} for:")
        lines.extend(_reason_lines(classes))
    return lines


#: The genuine risks, in the words a person uses. Keyed by
#: ``prospect_status_receipt.FIT_FAILURE_REASONS``; a reason without words renders a fixed
#: phrase, never the id.
RISK_WORDS: dict[str, str] = {
    "do-not-contact": "on the do-not-contact list",
    "disqualified": "at companies you disqualified",
    "outside-market": "at companies outside your target markets",
    "competitor": "at competitors",
    "regulator": "at regulators",
    "ruled-out": "at companies the research ruled out",
}


def _risk_lines(loaded: dict) -> list[str]:
    """People already in the sending tool at a company closed to sending. Only a person can take
    them out, so this is the one risk the lede names — with the reason, and nothing else."""
    parts = [
        (int(n), RISK_WORDS.get(str(reason), "at companies closed to sending"))
        for reason, n in loaded.items()
        if isinstance(n, int) and n > 0
    ]
    if not parts:
        return []
    total = sum(n for n, _ in parts)
    who = _count(total, "person", "people")
    if len(parts) == 1:
        detail = f", all {parts[0][1]}" if total > 1 else f", {parts[0][1]}"
    else:
        detail = ": " + ", ".join(f"{n:,} {w}" for n, w in sorted(parts, key=lambda p: -p[0]))
    return [
        f"Yours, before you start a sequence: take {who} out of the sending tool{detail}. "
        "They were loaded before their company was ruled out."
    ]


def compose_lede(
    readiness: Readiness,
    *,
    counts: dict[str, int],
    buckets: dict[str, int],
    sheet: str | None,
    now: str,
    go_live: str | None = None,
) -> list[str]:
    """The lines above the tables. Pure: every value is passed in; nothing is opened.

    ``counts`` — the contact statuses (``prospect_status``); ``buckets`` — the account
    receipt's ``to_dict()``; ``sheet`` — the review sheet's display name, or ``None``;
    ``go_live`` — the dashboard's observed sequence state, ``None`` on the terminal.

    What is NOT here, on purpose (operator direction 2026-09-24): counting discrepancies meant
    for whoever maintains the setup (``cross_check``). They are internal workings; the terminal
    prints them under "For the record" and the page on its Operator notes tab. The one risk a
    person must act on — someone already loaded in the sending tool at a company that is closed
    to sending — IS here, in plain words, because nothing else can take them out.
    """
    when = f"As of {now}."
    if readiness.state in ("ok", "stale"):
        when += f" The checks last ran {_when(readiness.ran_at)}."
    lines = [when, *_today_lines(readiness)]

    waiting = counts.get("waiting_on_you", 0)
    if waiting:
        where = (
            f"review sheet: {sheet}"
            if sheet
            else "the review sheet is built when the list is sorted"
        )
        lines.append(
            f"Yours ({waiting:,}): decide on {_count(waiting, 'contact', 'contacts')} — {where}"
        )
    else:
        lines.append("Yours: nothing is waiting on you.")
    lines.extend(_risk_lines(buckets.get("excluded_loaded") or {}))

    machine = [
        (buckets.get("failed_enrichment", 0), "finding a contact for {} "),
        (buckets.get("failed_intent", 0), "researching {} "),
        (buckets.get("not_routed", 0), "sorting {} "),
    ]
    parts = [tmpl.format(_count(n, "company", "companies")).strip() for n, tmpl in machine if n]
    fixing = counts.get("being_fixed", 0)
    if fixing:
        parts.append(f"re-drafting {_count(fixing, 'contact', 'contacts')}")
    lines.append("The machine's: " + (", ".join(parts) + "." if parts else "nothing queued."))

    loaded = counts.get("in_sending_tool", 0)
    if go_live in GO_LIVE_WORDS:
        lines.append(f"In the sending tool: {loaded:,} — {GO_LIVE_WORDS[go_live]}.")
    else:
        lines.append(
            f"In the sending tool: {loaded:,}. Nothing sends until you start a sequence there."
        )

    return lines
