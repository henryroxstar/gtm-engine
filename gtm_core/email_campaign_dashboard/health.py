"""PS20 — what the page must know about its own inputs before it shows a number.

Split from ``model.py`` (at its §R10 ceiling): the sending snapshot's age and readability,
and the one page-wide go-live call. Since Task 9 (PRD P1.6) also every file read the
renderer used to make for itself — the review sheet and the eval-labeling round, each
message's recipient list, the sequencer's capability rows — so ``render_html`` opens nothing.
"""

from __future__ import annotations

import csv as _csv
import re
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

from ..paths import _safe_segment
from ..prospect_lede import go_live
from ..prospects_consolidate import _prospects_dir
from .aggregate import _scope_figures
from .config import FIGURES_MAX_AGE_DAYS

#: A lane review sheet, as ``gtm_core.lanes`` names one: ``hold-<YYYY-MM-DD>.csv`` and the
#: ``.html`` rendered beside it. Anchored at both ends so the sibling artifacts in the same
#: directory — ``hold-decisions.jsonl``, ``hold-decisions-<date>-filled.csv`` — cannot match:
#: those are the operator's ANSWERS, and linking one as the sheet to fill in would send a
#: reader to a file whose decisions are already made.
_SHEET_RE = re.compile(r"^hold-(\d{4}-\d{2}-\d{2})\.(html|csv)$")
_LABELER_RE = re.compile(r"^labeler-(\d{4}-\d{2}-\d{2})-[a-z0-9]+\.html$")


def _parse_fetched(fetched) -> datetime | None:
    """The instant ``fetched`` names, or None when it cannot be parsed. A bare 10-char date
    is read as its midnight UTC. Shared by :func:`figures_age_days` and :func:`figures_date`
    so the two can never disagree about what "unparseable" means.
    """
    if not isinstance(fetched, str) or not fetched.strip():
        return None
    text = fetched.strip()
    try:
        if len(text) == 10:
            return datetime.combine(date.fromisoformat(text), datetime.min.time(), UTC)
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return when if when.tzinfo else when.replace(tzinfo=UTC)
    except ValueError:
        return None


def figures_age_days(fetched, now: datetime) -> int | None:
    """Whole days since ``fetched``; a bare date counts from midnight UTC. None = unknown —
    which also covers a ``fetched`` more than a day in the future: that far ahead is as
    untrustworthy as unparseable, so it counts as too stale to trust rather than silently
    fresh. Up to one day ahead is tolerated and clamped to 0: the skill writes ``fetched``
    as a bare LOCAL date, so an operator east of UTC (e.g. UTC+8, refreshing before 08:00
    local) writes a date that is, in UTC, still "tomorrow" — that must read as today, not
    as a false future.
    """
    when = _parse_fetched(fetched)
    if when is None:
        return None
    age = (now - when).days
    return None if age < -1 else max(age, 0)


def figures_date(fetched) -> str | None:
    """The calendar date (``YYYY-MM-DD``) ``fetched`` names, or None when unparseable — the
    renderer's one source of truth for what to show, so a stray word or a malformed stamp
    never prints as if it were a real date. Unlike :func:`figures_age_days`, a well-formed
    FUTURE date still parses here: this answers "what date does the string name", never
    "is that date trustworthy".
    """
    when = _parse_fetched(fetched)
    return None if when is None else when.date().isoformat()


def reconciliation_detail(reconciliation: dict) -> str:
    """The "missing from/extra in" clause for a genuine reconciliation mismatch — the one
    place that reads ``in_ledger_only``/``in_snapshot_only``, so the renderer never
    re-derives it."""
    parts = []
    if reconciliation.get("in_ledger_only"):
        parts.append(
            "missing from the live figures: " + ", ".join(reconciliation["in_ledger_only"])
        )
    if reconciliation.get("in_snapshot_only"):
        parts.append(
            "in the figures but not our records: " + ", ".join(reconciliation["in_snapshot_only"])
        )
    return "; ".join(parts)


def page_warnings(status: dict, reconciliation: dict, sum_ok: bool, now: datetime) -> list[str]:
    """The page-wide strip's reasons, in display order. Empty list = no strip."""
    snap = status.get("snapshot") or {}
    if snap.get("unreadable"):
        return ["unreadable"]
    reasons = []
    if not reconciliation.get("ok", True) or not sum_ok:
        reasons.append("records-disagree")
    if status.get("sequences"):
        age = figures_age_days(snap.get("fetched"), now)
        if age is None or age > FIGURES_MAX_AGE_DAYS:
            reasons.append("figures-old")
    return reasons


def _listed(c: dict) -> list[str]:
    return [s["sequence_id"] for k in ("sequences", "archived") for s in c.get(k, [])]


def shared_sequences(m: dict) -> dict[str, list[str]]:
    """Sequence id -> the titles of the in-scope campaigns listing it, for every id listed by
    more than one. Each of them sums the id's people into its own figures."""
    owners: dict[str, list[str]] = {}
    for c in m["campaigns"]["campaigns"]:
        for sid in _listed(c):
            owners.setdefault(sid, []).append(c.get("title") or c.get("slug", "?"))
    return {sid: names for sid, names in owners.items() if len(names) > 1}


def repeated_rows(m: dict) -> list[str]:
    """Ids the sending figures list more than once: their people are summed once per row."""
    seen = Counter(r.get("id") for r in m["status"].get("sequences", []) if r.get("id"))
    return sorted(sid for sid, n in seen.items() if n > 1)


def disagree_names(m: dict) -> list[str]:
    """The in-scope campaigns a `records-disagree` strip is about, in page order. A shared or
    repeated id is named only when the figures fail to add up — otherwise it is not what the
    strip is about."""
    ids = set((m.get("reconciliation") or {}).get("in_ledger_only") or [])
    if not _scope_figures(m)["sum_ok"]:
        ids |= set(shared_sequences(m)) | set(repeated_rows(m))
    return [
        c.get("title") or c.get("slug", "?")
        for c in m["campaigns"]["campaigns"]
        if ids & set(_listed(c))
    ]


def scoped_trust(m: dict, ids: set[str]) -> dict:
    """PS20 Task 2.0 — `warnings` and `reconciliation` for a campaign-scoped page. The sum check
    re-runs over the scoped figures; a records gap counts only when it names one of this page's
    sequences (a snapshot-only id belongs to no campaign); `figures-old` and `unreadable` stay,
    because every campaign reads the one snapshot."""
    own = sorted(set((m.get("reconciliation") or {}).get("in_ledger_only") or []) & ids)
    rec = {"ok": not own, "in_ledger_only": own, "in_snapshot_only": []}
    kept = [w for w in m.get("warnings") or [] if w != "records-disagree"]
    lead = ["records-disagree"] if own or not _scope_figures(m)["sum_ok"] else []
    return {"warnings": lead + kept, "reconciliation": rec}


def page_go_live(campaigns: dict, status: dict, contacted: dict | None) -> str:
    """Page-wide go-live: statuses and contacts from the SAME non-archived rows."""
    archived = {s["sequence_id"] for c in campaigns["campaigns"] for s in c.get("archived", [])}
    rows = [r for r in status.get("sequences", []) if r.get("id") not in archived]
    on_record = bool(campaigns.get("unlinked_sequences")) or any(
        c.get("sequences") for c in campaigns["campaigns"]
    )
    live = None if contacted is None else contacted["current"] + contacted["not_linked"]
    readable = not (status.get("snapshot") or {}).get("unreadable")
    return go_live([r.get("status") for r in rows], live, on_record, readable=readable)


def page_extras(
    profile: str,
    content_root: Path | None,
    status: dict,
    reconciliation: dict,
    sum_ok: bool,
    now: datetime,
) -> dict:
    """The model keys ``build_model`` merges in with ONE call (``model.py`` sits at its §R10
    ceiling): ``warnings`` (:func:`page_warnings`), and the two operator worksheets the page
    links — ``eval_labeler`` and ``review_sheet`` — read here, so ``render_html`` opens nothing
    (PS20 P1.6). ``eval_labeler`` and ``review_sheet`` are profile-wide, like ``inbound``:
    ``scope_to_campaign`` leaves them as built. ``warnings`` is not — a scoped page recomputes
    it with :func:`scoped_trust`."""
    return {
        "warnings": page_warnings(status, reconciliation, sum_ok, now),
        "eval_labeler": eval_labeler(profile, content_root),
        "review_sheet": review_sheet(profile, content_root),
    }


def list_rows(seq_dir: Path, name: str) -> list[dict]:
    """The parsed rows of one message's recipient CSV — ``msg["list_rows"]``.

    Carried PER MESSAGE, not as a profile-wide ``{email: …}`` map: ``scope_to_campaign``
    filters ``m["messages"]``, so a campaign page then sees only its own campaign's list
    membership. A profile-wide map would survive scoping and put another campaign's staging
    and sequence ids on this page. A missing or unreadable file is no rows, as it was when
    the worklist read the file itself. The name passes ``_safe_segment``, as every other read
    of these lists does (``gtm_core.cells``).
    """
    path = seq_dir / _safe_segment(name, "csv")
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            return list(_csv.DictReader(fh))
    except OSError:
        return []


def capability_rows_for(cap: dict | None) -> list:
    """The resolved capability rows for the newest ``capability_asserted`` row's provider —
    the ONE object the terminal preflight, the gate preview and the inbound panel render
    (``gtm_core.capability_preflight.capability_rows``; test plan §4.5). Resolved in the model
    because it opens ``gtm_core/sequencers.toml`` and ``render_html`` opens nothing (PS20
    P1.6); the panel renders these rows with ``render_summary``, unchanged."""
    from ..capability_preflight import capability_rows

    provider = str((cap or {}).get("provider") or "")
    try:
        return capability_rows(provider) if provider else []
    except Exception:  # noqa: BLE001 — a page must render even if the registry cannot load
        return []


def review_sheet(profile: str, content_root: Path | None) -> dict | None:
    """The newest lane review sheet on disk — its href, its date, and its own row count.

    Derived rather than written. The banner used to carry a hardcoded
    ``evals/lanes-hold-sheet.csv``, which was wrong twice over: no file has ever been
    written under that name (``gtm_core.lanes`` writes ``hold-<stamp>.csv``), and the page
    sits at ``content/<profile>/`` while the evals directory is one level further down at
    ``prospects/evals/`` — so even the correct filename would not have resolved. A dead link
    in a banner that asks for action is worse than no link: it reads as "the work is
    somewhere over there" and costs the operator the search to find out it is not.

    Prefers the rendered ``.html`` over the ``.csv`` of the same date — that is the sheet a
    person reviews; the CSV is the data behind it. The row count comes from the CSV either
    way, because counting ``<tr>`` in a rendered page counts its header and group rows too.

    Every scope's page is written to ``_prospects_dir(...).parent`` (see
    ``render.page_path``), so one relative prefix is correct for the rollup and the
    per-campaign pages alike. Moved from ``render.py`` (PS20 Task 9), body unchanged.
    """
    evals = _prospects_dir(profile, content_root) / "evals"
    if not evals.is_dir():
        return None
    dated: dict[str, dict[str, Path]] = {}
    for p in evals.iterdir():
        hit = _SHEET_RE.match(p.name)
        if hit:
            dated.setdefault(hit.group(1), {})[hit.group(2)] = p
    if not dated:
        return None
    stamp = max(dated)
    pick = dated[stamp].get("html") or dated[stamp]["csv"]
    csv = dated[stamp].get("csv")
    rows = None
    if csv:
        # Header excluded. A blank trailing line would otherwise count as a row and report
        # a sheet one bigger than it is.
        rows = max(
            len([ln for ln in csv.read_text(encoding="utf-8").splitlines() if ln.strip()]) - 1, 0
        )
    return {"href": f"prospects/evals/{pick.name}", "stamp": stamp, "rows": rows}


def eval_labeler(profile: str, content_root: Path | None) -> dict | None:
    """The newest email-eval labeling page on disk, and how much of it is still unanswered.

    The sibling of :func:`review_sheet`, added 2026-09-22 for the reason that one exists:
    the lane hold sheet carried an action-required banner for months, and the eval round —
    the round that decides whether a rule is KEPT or RETIRED — had none. It was invoked from
    memory, so it ran roughly never, and four rules sat reading ``delete-candidate`` for want
    of a single afternoon nobody was ever prompted to spend. Since PS20 Task 9 it is a line
    in the Operator notes panel's Maintenance card, not a banner.

    Counts come from the markdown sheet rather than the rendered page, because a pre-filled
    answer is a literal ``Y``/``N`` on the ``send_it:`` line there and an unanswered one is
    ``___`` — cheap to read and impossible to confuse with page markup. The two numbers are
    reported separately on purpose: the blind rows are the only ones that can tell you
    whether the pre-filled ones were any good. Moved from ``render.py``, body unchanged.
    """
    evals = _prospects_dir(profile, content_root) / "evals"
    if not evals.is_dir():
        return None
    pages: dict[str, Path] = {}
    for p in evals.iterdir():
        hit = _LABELER_RE.match(p.name)
        if hit:
            pages.setdefault(hit.group(1), p)
    if not pages:
        return None
    stamp = max(pages)
    prefilled = blind = 0
    sheet = evals / f"sheet-{stamp}.md"
    if sheet.is_file():
        for line in sheet.read_text(encoding="utf-8").splitlines():
            if line.startswith("`send_it:`"):
                if "___" in line:
                    blind += 1
                else:
                    prefilled += 1
    return {
        "href": f"prospects/evals/{pages[stamp].name}",
        "stamp": stamp,
        "prefilled": prefilled,
        "blind": blind,
    }
