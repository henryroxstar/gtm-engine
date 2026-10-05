"""Where these numbers come from, and which sections are read rather than typed.

WHY THIS EXISTS (2026-09-30, G2/G8). ``sequence-stats.json`` — every sent, replied and bounced
number on this page — is written by an AGENT following skill prose, with a date it types. One
date covered sixteen sequences on a live tenant, although the skill refreshed only the ones it
touched. Nothing on the page distinguished that from a figure re-read out of a file on every
render, and a reader has no way to guess.

Three things ship here.

* :data:`SOURCES` — one row per source: an operator label, the input globs it covers, an optional
  as-of reader, and a state from a CLOSED list. It is the operator's answer to "can I trust this?"
* :data:`SECTION_KIND` — a word per ``config.SECTIONS`` id: ``live``, ``static`` or
  ``agent-written``. Written by hand and checked against the map: the contract test proves the
  KEYS cover the registry in both directions and that every value agrees with the section's map.
  It marks nothing else on the page.
* **The section -> source MAPPING** lives in :mod:`.section_sources`, with its perturbation oracle
  in tests/contracts/test_dashboard_section_sources.py (change one source's input, re-render,
  assert the changed sections are a subset of its mapped sections). :data:`SECTION_KIND` is held
  to that map: a section whose map holds a typed source (:data:`TYPED_SOURCES`: the sending
  snapshot and the hand-recorded outcomes file) is ``agent-written``, because ONE typed input
  is enough for a number to be as old as someone's last entry.

**As-of dates come from a source's own CONTENT, never from a file's mtime.** The sending
figures read their snapshot's ``fetched``; the pre-flight row its report's ``ran_at``; the sorted
list its newest lane ``stamp``; the ledger row its history's newest ``ts``; the outcomes row its
newest ``ts``; the review row the dated name the writer chose. A source whose file records no
date says "no date in the file" and is never called more than present; one whose date is there
but unreadable is ``unknown``. Only the sending figures have an age limit — nothing here invents
a second one. A clone, a ``cp -r``
or a restore rewrites every mtime in the tree at once, so an mtime-derived "as of" is confidently
wrong for everything simultaneously — the same failure ``page_inputs`` uses digests to avoid. The
contract test asserts that by AST over this module, not only by value.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import health
from .config import BENCHMARKS_RESEARCHED, FIGURES_MAX_AGE_DAYS, input_globs
from .format import _e

#: The group's id in ``config.OPS_GROUPS``. The group is ALWAYS open (``views_ops._ops_view``): a
#: collapsed copy of the one table that says what is stale defeats it. A card with no declared
#: home is how the Operator notes tab sprawled one finding at a time (PS20 root cause 1), so this
#: is an edit there.
GROUP_ID = "sources"

#: What a source's state may say. Closed, so a typo or a new state cannot reach the page as a
#: word that looks like it means something — anything outside this renders "unknown".
#:
#: They are five different pieces of work, which is the whole reason they are not one word:
#: ``old`` means refresh it, ``missing`` means set it up or find out why it is gone, ``static``
#: means it is a researched constant and no refresh exists, ``current`` means it carries a date
#: this page read (and, for the sending figures only, that the date is inside the limit), and
#: ``undated`` means the file is there but records no date at all — present, with nothing to
#: compare it against. ``undated`` is never ``old``: only the sending figures have an age limit.
#: A sixth word, ``unknown``, is produced but deliberately not listed: it is what a source says
#: when its date or its file cannot be read, and listing it would let a typo pass for it.
SOURCE_STATES = ("current", "old", "missing", "static", "undated")

#: What a section's numbers ARE. ``live`` is re-read from the tenant's data on every render;
#: ``static`` is a published reference constant compiled into the code; ``agent-written`` means a
#: person or an agent typed the input this section reads.
SECTION_KINDS = ("live", "static", "agent-written")


class DateUnreadable(ValueError):
    """A source records a date this page cannot read. Never a crash and never a silent "no
    date": the state becomes ``unknown`` — a different finding from a file that holds none."""


def _day(raw) -> str | None:
    """The calendar day ``raw`` names, through the figures' OWN parser (:func:`health.figures_date`)
    — one definition of "a readable date" for every as-of on the page. ``None`` when ``raw`` is
    absent or blank (no date recorded); :class:`DateUnreadable` when it is there but does not
    parse — text that is not a date, or a value that is not text at all — so a typo cannot read as
    "nothing to check"."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    day = health.figures_date(raw)
    if day is None:
        raise DateUnreadable(str(raw)[:40])
    return day


def _figures_as_of(m: dict) -> str | None:
    """The snapshot's own ``fetched``, through the one resolved state — never re-parsed here, so
    the table, the header and the strip cannot disagree about what date the figures carry."""
    return (m.get("figures") or {}).get("date")


def _checks_as_of(m: dict) -> str | None:
    """The pre-flight report's own ``ran_at``. A check's age is a fact it records about itself.

    ``m["readiness"]`` is the object ``prospect_readiness.load_readiness`` returned; it has no
    ``ran_at`` when the report is absent or unreadable, which is "no date", not a guess.
    """
    ready = m.get("readiness")
    if getattr(ready, "state", None) == "unreadable":
        raise DateUnreadable("the pre-flight report")
    return _day(getattr(ready, "ran_at", "") or None)


def _review_as_of(m: dict) -> str | None:
    """The newest review sheet's own dated FILENAME, as ``health.review_sheet`` already parsed
    it. A name the writer chose, not a timestamp the filesystem assigned."""
    return _day((m.get("review_sheet") or {}).get("stamp"))


def _benchmarks_as_of(_m: dict) -> str:
    return BENCHMARKS_RESEARCHED


def newest_row_day(path: Path, key: str) -> str | None:
    """The newest ``key`` date among the rows of a JSONL file — the file's OWN content.

    ``None`` when the file holds no dated row (empty, or no row carries ``key``).
    :class:`DateUnreadable` when rows carry a date but NONE of them reads, or a line is not
    JSON at all and nothing else dated the file: one unreadable row among readable ones does
    not hide the newest readable date, because an older as-of can only make a file look
    older, never newer. A file that is absent has no date (the caller already settled that it
    is missing); one that exists and cannot be opened is unreadable.
    """
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DateUnreadable(path.name) from exc
    best: str | None = None
    bad = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(row, dict) or not row.get(key):
            continue
        try:
            day = _day(row[key])
        except DateUnreadable:
            bad += 1
            continue
        if day and (best is None or day > best):
            best = day
    if best is None and bad:
        raise DateUnreadable(path.name)
    return best


#: About a dozen rows, in reading order: the figures a reader distrusts first, then the records
#: they are reconciled against, then the inputs behind them. Every glob here is also in
#: ``INPUT_GLOBS``/``PROFILE_FILES`` and every entry there belongs to exactly one row — both
#: directions are contract-tested, because the reverse is the one that decays: a glob added with
#: no row leaves the table reading complete while being short.
SOURCES: dict[str, dict] = {
    "figures": {
        "label": "The sending figures (contacted, replied, bounced)",
        "globs": (
            "prospects/sequences/.pool/sequence-stats.json",
            "prospects/sequences/.pool/sequence-state.json",
        ),
        "as_of": _figures_as_of,
    },
    "outcomes": {
        "label": "Replies and meetings recorded by hand",
        "globs": ("outcomes.jsonl",),
        "as_of": None,
        "rows": ("outcomes.jsonl", "ts"),
    },
    "plans": {
        "label": "Campaign plans (targets, sending window, which sequences belong to which)",
        "globs": ("plans/campaigns/*.campaign.toml",),
        "as_of": None,
    },
    "sorted-list": {
        "label": "The sorted list (who is in which lane)",
        "globs": ("prospects/evals/lanes-state.jsonl",),
        "as_of": None,
        "rows": ("prospects/evals/lanes-state.jsonl", "stamp"),
    },
    "checks": {
        "label": "The pre-send checks",
        "globs": ("preflight/latest.json", "prospects/sequences/.pool/lint-*.json"),
        "as_of": _checks_as_of,
    },
    "ledger": {
        "label": "The account ledger and the run history",
        "globs": ("prospects/latest.json", "history.jsonl"),
        "as_of": None,
        "rows": ("history.jsonl", "ts"),
    },
    "pool": {
        "label": "The sending pool and its lists",
        "globs": (
            "prospects/sequences/cells.toml",
            "prospects/sequences/*.csv",
            "prospects/sequences/.pool/master-list.csv",
            "prospects/sequences/.pool/needs-verification.csv",
            "prospects/sequences/.pool/enrichment-queue.csv",
            "prospects/sequences/.pool/suppression.csv",
            "prospects/imports/*.csv",
            "prospects/prospects-*-hubspot.csv",
        ),
        "as_of": None,
    },
    "copy": {
        "label": "Outreach copy (sequence specs and 1:1 packs)",
        "globs": (
            "prospects/sequences/*.md",
            "accounts/*/prospects-*-outreach-*.md",
            "accounts/*/email-*.md",
        ),
        "as_of": None,
    },
    "review": {
        "label": "The review round (hold sheet, labelling sheet, re-target queue)",
        "globs": (
            "prospects/evals/retarget-queue-*.jsonl",
            "prospects/evals/labeler-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*.html",
            "prospects/evals/sheet-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md",
            "prospects/evals/hold-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].csv",
            "prospects/evals/hold-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html",
        ),
        "as_of": _review_as_of,
    },
    "knowledge": {
        "label": "Company and brand knowledge (markets, seats, palette)",
        "globs": ("PROFILE.md", "knowledge/role-vocabulary.toml", "knowledge/BRAND.toml"),
        "as_of": None,
    },
    "benchmarks": {
        # No glob at all, and that is the finding: these are compiled into the code, so no
        # refresh command exists and `--check-fresh` can never see them move.
        "label": "Published reply-rate reference figures (not from your own data)",
        "globs": (),
        "as_of": _benchmarks_as_of,
    },
}


def _figures_state(m: dict) -> str:
    """The figures get their state from the ONE resolved ``figures_state``, so the table cannot
    call them current while the header prints an age past the limit.

    The word comes from the STATE first: an unreadable file is ``unknown`` whatever the age test
    would have said, and a model with no resolved figures at all (or a state ``FIGURES_STATES``
    does not define) is ``unknown`` too, never ``current``. Only then the age: an undated or
    too-far-future state carries ``over_limit`` (its age is unknown, which counts as too old to
    trust), so it reads ``old`` through the same line as a figure past the limit.
    """
    fig = m.get("figures") or {}
    state = fig.get("state")
    if state == "none":
        return "missing"
    if state not in health.FIGURES_STATES or state == "unreadable":
        return "unknown"  # not in SOURCE_STATES on purpose: we cannot say, and say so
    return "old" if fig.get("over_limit") else "current"


def _dated(state: str, as_of: str | None) -> str:
    """``current`` is a claim about a date. A source that is present but whose own content names
    none is ``undated`` — the same fact the "As of" cell already prints as "no date in the file"."""
    return "undated" if state == "current" and not as_of else state


def figures_row(m: dict) -> dict:
    """The sending-figures row of the table, built from ``m["figures"]`` and nothing else.

    Public because the row depends on WHICH sequences the page covers: a campaign page re-judges
    ``figures`` for its own sequences (:func:`figure_ages.rescope`) and must re-derive this row
    from the re-judged value, or the table keeps the whole profile's verdict under a header that
    says something else.
    """
    as_of = _figures_as_of(m)
    return {
        "label": SOURCES["figures"]["label"],
        "state": _dated(_figures_state(m), as_of),
        "as_of": as_of,
    }


def _as_of(src: dict, m: dict, root: Path) -> str | None:
    """A source's own as-of day, from its reader or its dated rows, else ``None`` (the file
    records no date). May raise :class:`DateUnreadable`."""
    if src["as_of"]:
        return src["as_of"](m)
    if src.get("rows"):
        rel, key = src["rows"]
        return newest_row_day(root / rel, key)
    return None


def source_states(m: dict, profile: str, content_root) -> dict[str, dict]:
    """``{key: {"label", "state", "as_of"}}`` — resolved in the MODEL, where the filesystem is.

    ``render_html`` opens nothing (PS20 P1.6), so presence is settled here rather than in the
    view. Presence is ``is_file()`` through the same ``_resolve`` the inventory uses — no mtime,
    no size, nothing that a clone or a restore rewrites. The as-of day is read from the source's
    own content, and only for a source that is present: a missing file has no date to be wrong
    about. A date that is there but cannot be read makes the state ``unknown`` — never
    ``current`` on the strength of a file whose date this page could not check.
    """
    from ..page_inputs import _profile_rel, _profile_root, _resolve

    root, tracked = input_globs(profile, content_root)
    claimed = {g for s in SOURCES.values() for g in s["globs"]}
    rosters = [g for g in tracked if g not in claimed]
    out: dict[str, dict] = {}
    for key, src in SOURCES.items():
        if key == "benchmarks":
            state = "static"
        elif key == "figures":
            state = _figures_state(m)
        elif key == "knowledge":
            base = _profile_root(profile) if profile else None
            rels = [_profile_rel(g) for g in src["globs"]]
            state = (
                "current"
                if base is not None and any(r and (base / r).is_file() for r in rels)
                else "missing"
            )
        else:
            globs = list(src["globs"]) + (rosters if key == "pool" else [])
            state = "current" if _resolve(root, globs) else "missing"
        as_of = None
        if state != "missing":
            try:
                as_of = _as_of(src, m, root)
            except DateUnreadable:
                state = "unknown"
        out[key] = {"label": src["label"], "state": _dated(state, as_of), "as_of": as_of}
    return out


#: The sources a person or an agent TYPES: the sending snapshot (an agent writes it with a date it
#: records) and the outcomes file (replies and meetings recorded by hand, as the source row says).
#: The one definition both :data:`SECTION_KIND` and its contract test hold sections to.
TYPED_SOURCES = ("figures", "outcomes")

#: Which sections are read from the data, which are a researched constant, and which read an
#: input a person or an agent TYPED. Keys are the ``config.SECTIONS`` ids, in both directions
#: (tests/contracts/test_dashboard_provenance.py); each value is checked against the section's
#: source map (tests/contracts/test_dashboard_section_sources.py), so a section that reads a
#: :data:`TYPED_SOURCES` source cannot be labelled ``live``.
SECTION_KIND: dict[str, str] = {
    # Overview
    "lede": "agent-written",
    "campaign-lines": "agent-written",  # contacted/replied come from the typed snapshot
    "accounts-funnel": "live",
    "contacts-by-status": "live",
    "ready-to-send": "live",
    "actions-required": "agent-written",
    # Accounts
    "filter": "live",
    "account-tiles": "live",
    "account-table": "live",
    # Emails
    "email-portfolio": "live",
    "email-table": "agent-written",
    "hand-sent": "live",
    "packs-list": "live",
    # Results
    "results-figures": "agent-written",
    "campaign-results": "agent-written",
    "when-we-know": "agent-written",
    "voice-of-market": "agent-written",  # reads only the hand-recorded outcomes file
    # Insights
    "small-numbers": "live",
    "learnings": "live",
    "angle-heatmap": "live",
    "sentiment-triage": "agent-written",  # reads only the hand-recorded outcomes file
    # Operator notes — the group ids
    "numbers": "agent-written",
    "before-sending": "agent-written",
    "sending-setup": "agent-written",
    "list-quality": "live",
    "email-quality": "live",
    "experiment-design": "agent-written",
    "replies": "live",
    "maintenance": "live",
    GROUP_ID: "live",
    # Operator notes — the blocks
    "cross-check": "live",
    "reconciliation": "agent-written",
    "shared-sequences": "agent-written",
    "unlinked": "agent-written",
    "list-vs-provider": "agent-written",
    "sent": "agent-written",
    "re-push": "live",
    "holding-up": "live",
    "nothing-outstanding": "live",
    "load-files": "agent-written",
    "ceiling-tile": "live",
    "compliance": "live",
    "setup-tiles": "agent-written",
    "sequence-table": "agent-written",
    "forecast": "agent-written",
    "pool": "live",
    "roster-notes": "live",
    "segment-mix": "live",
    "needs-address": "live",
    "finding-new-people": "live",
    "subjects": "live",
    "opening-lines": "live",
    "capability-spread": "live",
    "judge-notes": "live",
    "checks-detail": "live",
    "varies": "live",
    "grid": "live",
    "readable-difference": "agent-written",
    "benchmarks": "static",
    "experiment-notes": "live",
    "can-answer": "live",
    "inbound-health": "live",
    "maintenance-lines": "live",
    "sources-table": "live",
    "section-kinds": "static",
}

_STATE_WORDS = {
    "current": "current",
    "undated": "present — it records no date",
    "old": "old — refresh it",
    "missing": "missing",
    "static": "static — a researched constant, no refresh exists",
    "unknown": "unknown — it could not be read",
}


def _as_of_words(got: dict) -> str:
    """The "As of" cell. A source with no readable date says so — a bare dash next to a state would
    read as a date the page checked — and a missing or unreadable one has nothing to say."""
    if got.get("as_of"):
        return str(got["as_of"])
    return "no date in the file" if got.get("state") == "undated" else "—"


def sources_table(m: dict) -> str:
    """ "Where these numbers come from" — one row per source, in ``SOURCES`` order."""
    rows = ""
    for key, src in SOURCES.items():
        got = (m.get("sources") or {}).get(key) or {}
        state = got.get("state")
        rows += (
            f"<tr><td>{_e(src['label'])}</td>"
            f"<td class='muted'>{_e(_as_of_words(got))}</td>"
            f"<td class='muted'>{_e(_STATE_WORDS.get(state, 'unknown'))}</td></tr>"
        )
    return f"""
      <div class="card glass-panel animate-on-load anim-up-lg" style="--anim-delay: 50ms;">
        <h2>Where these numbers come from</h2>
        <p class="note">Every figure on this page is read from one of these. "As of" is each
        source's own recorded date, never the time its file was last written — a restore or a
        copy rewrites every file time at once.
        The sending figures are flagged after {FIGURES_MAX_AGE_DAYS} days; nothing else on this list has an age limit.</p>
        <table><thead><tr><th>Source</th><th>As of</th><th>Can you trust it?</th></tr></thead>
        <tbody>{rows}</tbody></table>
      </div>"""


def section_kinds_table(_m: dict) -> str:
    """The compact second table: which sections are read, which are constants, which are typed.

    Grouped BY KIND rather than listed by id, because the question a reader has is "which of
    these did a person type?" — and an alphabetical list of sixty ids does not answer it.
    """
    rows = ""
    for kind in SECTION_KINDS:
        ids = sorted(sid for sid, k in SECTION_KIND.items() if k == kind)
        rows += (
            f"<tr><td>{_e(kind)}</td><td class='num-cell'>{len(ids)}</td>"
            f"<td class='muted'>{_e(', '.join(ids))}</td></tr>"
        )
    return f"""
      <div class="card glass-panel animate-on-load anim-up-lg" style="--anim-delay: 50ms;">
        <h2>Which sections are read, and which are typed</h2>
        <p class="note"><strong>live</strong> is re-read from your own data on every render.
        <strong>static</strong> is a published reference figure compiled into this page, so no
        refresh exists for it. <strong>agent-written</strong> means the input it reads was typed
        by a person or an agent — accurate as of whenever that happened, which the row above
        says. A section that reads a typed input alongside live ones is listed as agent-written.</p>
        <table><thead><tr><th>Kind</th><th>Sections</th><th>Which</th></tr></thead>
        <tbody>{rows}</tbody></table>
      </div>"""
