"""Learning cells — the unit of analysis a PMF question can actually be asked of.

A *sequence* is not a readable experimental unit. It bundles segment + seat + copy +
list source + send window, and all five vary together, so no per-sequence number is
ever a clean read. The 2026-08-18 seat split made this sharper rather than softer:
``Enterprise / Technical`` and ``Enterprise / Exec`` now differ in **audience and
message at the same time**, so a gap between them confounds the two and cannot be
read as either.

A **cell** is the smallest unit that holds one audience and one message::

    cell_id = "<segment>:<seat>:<variant>"      # e.g. "enterprise:technical:run500-enterprise-technical"

``segment`` comes from the enrolment CSV's own column, ``seat`` from
:func:`outreach_pack_linter.seat_of` on the recipient's title (the same resolver the
persona-lead gate uses, so the page and the gate can never disagree about who someone
is), and ``variant`` from the spec filename — one spec file is one variant by
construction since the 2026-08-18 split, which is what makes the filename a sound key.

The cell id is written onto an outcome row's existing ``tags`` list
(:mod:`gtm_core.outcomes`), whose docstring already names that field "the learning
axis". No schema change: the axis was designed for exactly this and never populated.

Stdlib-only and provider-free, like the dashboards that consume it. The
sequence → (list, spec) join is *data*, not inference: it is read from
``prospects/sequences/cells.toml`` rather than guessed from filenames, because a
wrong join silently attributes one seat's replies to another — the same class of
error as the wrong-copy incident this model exists to make visible.

CLI::

    python -m gtm_core.cells --profile P                 # cell table as JSON
    python -m gtm_core.cells --profile P --format text   # human-readable
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tomllib
from pathlib import Path

from gtm_core.list_fit import role_fit
from gtm_core.paths import _safe_segment
from gtm_core.prospects_consolidate import _prospects_dir

# The persona-axis resolver lives with the linter that enforces it. Importing it here
# (rather than re-deriving a seat map) is deliberate: two implementations of "which
# seat is this person" would drift, and the drift would be invisible — the page would
# report cells the gate never checked.
_LINTER_DIR = Path(__file__).resolve().parent.parent / "tests" / "linter"
if str(_LINTER_DIR) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(_LINTER_DIR))

from outreach import seat_of  # noqa: E402

#: Cell dimension used when the CSV carries no segment for a row. Never guessed —
#: an unknown segment is reported as unknown so the gap is visible in the table.
UNKNOWN = "unknown"

#: Power/interval arithmetic lives in :mod:`gtm_core.power`, a stdlib-only leaf, and is
#: re-exported here so every existing caller (and ``tests/test_cells.py``, which reaches
#: them as ``cells.wilson`` / ``cells.detectable_lift``) is unchanged. Extracted 2026-09-21
#: because importing this module drags in the ``sys.path`` insert above, and a caller that
#: only wants to ask "is this n powered?" should not have to take that on. ONE
#: implementation: a second copy would let two modules disagree about whether the same n
#: supports the same claim.
from .power import Z_CONF, Z_POWER, detectable_lift, wilson  # noqa: E402,F401


def cells_map_path(profile: str, content_root: Path | None = None) -> Path:
    return _prospects_dir(profile, content_root) / "sequences" / "cells.toml"


def variant_of(spec: str) -> str:
    """The variant key for a spec file: its stem, minus the ``spec-`` prefix and any
    trailing ``-YYYY-MM-DD``.

    One spec file is one variant — the property the 2026-08-18 split established and
    that :mod:`tests.linter.merge_render_linter` now depends on, since a spec holding
    two variants renders both against every row and mis-attributes the failures.
    """
    stem = Path(spec).stem
    if stem.startswith("spec-"):
        stem = stem[len("spec-") :]
    parts = stem.split("-")
    # Trailing ISO date: <...>-2026-08-18
    if len(parts) >= 4 and parts[-3].isdigit() and len(parts[-3]) == 4:
        parts = parts[:-3]
    return "-".join(parts) or stem


#: The overlay dimension's value for a run on the tenant's own targeting. Spelled rather
#: than left empty so a historical cell id stays readable and never silently changes identity
#: when the dimension was added — ``base:enterprise:cto:v1``, not ``:enterprise:cto:v1``.
BASE_OVERLAY = "base"


def cell_id(segment: str, seat: str | None, variant: str, overlay: str | None = None) -> str:
    """The unit of analysis: one audience, one message, under one targeting definition.

    ``overlay`` names the experiment overlay the run resolved against
    (:mod:`gtm_core.experiments`), or :data:`BASE_OVERLAY` for the tenant's live targeting.
    It is a full dimension rather than a tag because two cells differing ONLY in it are a
    clean ICP comparison — same audience description, same seat, same copy, two definitions
    of who to target — and :func:`_mark_comparability` can then say so without knowing
    anything about experiments.
    """
    ov = (overlay or BASE_OVERLAY).strip().lower() or BASE_OVERLAY
    return f"{ov}:{(segment or UNKNOWN).strip().lower()}:{seat or UNKNOWN}:{variant}"


def load_cell_map(profile: str, content_root: Path | None = None) -> list[dict]:
    """The sequence → (csv, spec) join, from ``sequences/cells.toml``.

    Fail-closed: a missing or malformed file yields no sources rather than a guess.
    Guessing this join would attribute one seat's replies to another.
    """
    path = cells_map_path(profile, content_root)
    if not path.is_file():
        return []
    try:
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    out = []
    for row in doc.get("sequence", []):
        if row.get("id") and row.get("csv") and row.get("spec"):
            out.append(
                {
                    "sequence_id": row["id"],
                    "csv": row["csv"],
                    "spec": row["spec"],
                    "title": row.get("title", ""),
                    "campaign": row.get("campaign", ""),
                    # personalised | generic | repair. Optional; a missing lane reads as
                    # "unregistered" downstream, never as personalised.
                    "lane": str(row.get("lane", "") or "").strip().lower(),
                    # The experiment overlay this list was built under, if any. Absent means
                    # the tenant's live targeting, which is the overwhelming majority of rows
                    # and the reason the default is a value rather than a blank.
                    "overlay": str(row.get("overlay", "") or "").strip().lower() or BASE_OVERLAY,
                }
            )
    return out


def lane_by_sequence(profile: str, content_root: Path | None = None) -> dict[str, str]:
    """``sequence_id -> lane`` for every registered list that declares one."""
    out: dict[str, str] = {}
    for src in load_cell_map(profile, content_root):
        if src["lane"]:
            out.setdefault(src["sequence_id"], src["lane"])
    return out


def cells_by_sequence(profile: str, content_root: Path | None = None) -> dict[str, list[str]]:
    """``sequence_id -> [cell_id, …]`` — which cells a sequence's lists enrol. A sequence
    whose lists resolve to exactly one cell can take a ``cell:`` tag on an aggregate row."""
    base = _prospects_dir(profile, content_root) / "sequences"
    out: dict[str, list[str]] = {}
    for src in load_cell_map(profile, content_root):
        ids = out.setdefault(src["sequence_id"], [])
        for cid in _enrolled_cells(src, base):
            if cid not in ids:
                ids.append(cid)
    return out


def lane_index(profile: str, content_root: Path | None = None) -> dict[str, str]:
    """``lowercased email -> lane``, first list to claim an address wins (as ``email_index``)."""
    base = _prospects_dir(profile, content_root) / "sequences"
    index: dict[str, str] = {}
    for src in load_cell_map(profile, content_root):
        if not src["lane"]:
            continue
        csv_path = base / _safe_segment(src["csv"], "csv")
        if not csv_path.is_file():
            continue
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                email = (row.get("email") or "").strip().lower()
                if email:
                    index.setdefault(email, src["lane"])
    return index


def list_overlaps(profile: str, content_root: Path | None = None) -> list[dict]:
    """Addresses enrolled in more than one registered list — the duplicate-enrolment defect
    ``email_index``'s first-claim rule hides. Reported as counts per list pair, never as
    addresses (PII stays under content/)."""
    base = _prospects_dir(profile, content_root) / "sequences"
    seen: dict[str, str] = {}
    pairs: dict[tuple[str, str], int] = {}
    for src in load_cell_map(profile, content_root):
        csv_path = base / _safe_segment(src["csv"], "csv")
        if not csv_path.is_file():
            continue
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                email = (row.get("email") or "").strip().lower()
                if not email:
                    continue
                if email in seen and seen[email] != src["csv"]:
                    key = (seen[email], src["csv"])
                    pairs[key] = pairs.get(key, 0) + 1
                else:
                    seen.setdefault(email, src["csv"])
    return [{"first": a, "also_in": b, "emails": n} for (a, b), n in sorted(pairs.items())]


def _enrolled_cells(src: dict, base: Path) -> dict[str, dict]:
    """Cell rows for one sequence, counted from its enrolment CSV."""
    csv_path = base / _safe_segment(src["csv"], "csv")
    variant = variant_of(src["spec"])
    cells: dict[str, dict] = {}
    if not csv_path.is_file():
        return cells
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            segment = (row.get("segment") or UNKNOWN).strip().lower() or UNKNOWN
            seat = seat_of(row.get("title") or "")
            overlay = src.get("overlay") or BASE_OVERLAY
            cid = cell_id(segment, seat, variant, overlay)
            cell = cells.setdefault(
                cid,
                {
                    "cell_id": cid,
                    "overlay": overlay,
                    "segment": segment,
                    "seat": seat or UNKNOWN,
                    "variant": variant,
                    "sequence_id": src["sequence_id"],
                    "lane": src.get("lane") or "unregistered",
                    "enrolled": 0,
                    "suppressed": 0,
                },
            )
            cell["enrolled"] += 1
            if (row.get("suppression") or "").strip():
                cell["suppressed"] += 1
    for cell in cells.values():
        cell["sendable"] = cell["enrolled"] - cell["suppressed"]
    return cells


def email_index(profile: str, content_root: Path | None = None) -> dict[str, str]:
    """``lowercased email -> cell_id`` across every enrolment list in the cell map.

    The join key for attributing a provider reply to a cell. An address enrolled in
    two lists maps to the FIRST list that claims it and is not silently overwritten —
    a duplicate enrolment is a list defect, and letting the last writer win would hide
    it while quietly moving a reply between cells.
    """
    base = _prospects_dir(profile, content_root) / "sequences"
    index: dict[str, str] = {}
    for src in load_cell_map(profile, content_root):
        csv_path = base / _safe_segment(src["csv"], "csv")
        if not csv_path.is_file():
            continue
        variant = variant_of(src["spec"])
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                email = (row.get("email") or "").strip().lower()
                if not email or email in index:
                    continue
                segment = (row.get("segment") or UNKNOWN).strip().lower() or UNKNOWN
                index[email] = cell_id(
                    segment,
                    seat_of(row.get("title") or ""),
                    variant,
                    src.get("overlay") or BASE_OVERLAY,
                )
    return index


def build_cells(
    profile: str,
    content_root: Path | None = None,
    *,
    outcome_rows: list[dict] | None = None,
    baseline: float = 0.059,
) -> dict:
    """The cell table: enrolment from the CSVs, results from the outcomes ledger.

    ``outcome_rows`` is injected rather than read here so this stays testable and so
    the caller controls the time window. A row joins a cell by carrying ``cell:<id>``
    in its ``tags``.
    """
    from gtm_core.outcomes import (  # local import: keeps module import cheap
        ATTEMPT_OUTCOMES,
        MEETING_OUTCOMES,
        REPLY_OUTCOMES,
    )

    base = _prospects_dir(profile, content_root) / "sequences"
    sources = load_cell_map(profile, content_root)

    cells: dict[str, dict] = {}
    for src in sources:
        for cid, cell in _enrolled_cells(src, base).items():
            if cid in cells:
                cells[cid]["enrolled"] += cell["enrolled"]
                cells[cid]["suppressed"] += cell["suppressed"]
                cells[cid]["sendable"] += cell["sendable"]
            else:
                cells[cid] = cell
    for cell in cells.values():
        cell.update({"sent": 0, "replied": 0, "positive": 0, "meetings": 0})

    for row in outcome_rows or []:
        outcome = str(row.get("outcome", "")).strip().lower()
        try:
            value = float(row.get("value", 1) or 0)
        except (TypeError, ValueError):
            value = 1.0
        for tag in row.get("tags") or []:
            if not isinstance(tag, str) or not tag.startswith("cell:"):
                continue
            cell = cells.get(tag[len("cell:") :])
            if cell is None:
                continue
            if outcome in ATTEMPT_OUTCOMES:
                cell["sent"] += value
            if outcome in REPLY_OUTCOMES:
                cell["replied"] += value
            if outcome == "positive_reply":
                cell["positive"] += value
            if outcome in MEETING_OUTCOMES:
                cell["meetings"] += value

    for cell in cells.values():
        sent = cell["sent"]
        cell["reply_rate"] = round(cell["replied"] / sent, 4) if sent else None
        ci = wilson(cell["replied"], sent)
        cell["ci95"] = [round(ci[0], 4), round(ci[1], 4)] if ci else None
        cell["detectable_lift"] = detectable_lift(sent or cell["sendable"], baseline)
        # Whether the cell can be compared with any other at all. Two cells that
        # differ in BOTH seat and variant are confounded by construction.
        cell["comparable_on"] = []

    ordered = sorted(
        cells.values(),
        key=lambda c: (c.get("overlay", BASE_OVERLAY), c["segment"], c["seat"], c["variant"]),
    )
    _mark_comparability(ordered)
    return {
        "profile": profile,
        "baseline": baseline,
        "sources": sources,
        "cells": ordered,
        "confounded": all(not c["comparable_on"] for c in ordered) and len(ordered) > 1,
        "by_lane": _by_lane(ordered, outcome_rows or []),
        "overlaps": list_overlaps(profile, content_root),
    }


def _by_lane(cells: list[dict], outcome_rows: list[dict]) -> dict[str, dict]:
    """The oracle the lane design exists for: reply rate per lane (personalised / repair /
    generic), with a Wilson interval. Cell-attributed rows are summed through their cells;
    an aggregate ``sent`` row that carries only ``lane:`` (a sequence spanning several
    cells) is counted once here and nowhere else."""
    from gtm_core.outcomes import ATTEMPT_OUTCOMES, REPLY_OUTCOMES  # local import, as above

    lanes: dict[str, dict] = {}

    def bucket(lane: str) -> dict:
        return lanes.setdefault(
            lane,
            {
                "lane": lane,
                "enrolled": 0,
                "sendable": 0,
                "sent": 0.0,
                "replied": 0.0,
                "positive": 0.0,
            },
        )

    for c in cells:
        b = bucket(c["lane"])
        for k in ("enrolled", "sendable", "sent", "replied", "positive"):
            b[k] += c[k]
    for row in outcome_rows:
        tags = [t for t in (row.get("tags") or []) if isinstance(t, str)]
        if any(t.startswith("cell:") for t in tags):
            continue  # already counted through its cell
        lane = next((t[len("lane:") :] for t in tags if t.startswith("lane:")), None)
        if not lane:
            continue
        outcome = str(row.get("outcome", "")).strip().lower()
        try:
            value = float(row.get("value", 1) or 0)
        except (TypeError, ValueError):
            value = 1.0
        b = bucket(lane)
        if outcome in ATTEMPT_OUTCOMES:
            b["sent"] += value
        if outcome in REPLY_OUTCOMES:
            b["replied"] += value
        if outcome == "positive_reply":
            b["positive"] += value
    for b in lanes.values():
        sent = b["sent"]
        b["reply_rate"] = round(b["replied"] / sent, 4) if sent else None
        ci = wilson(b["replied"], sent)
        b["ci95"] = [round(ci[0], 4), round(ci[1], 4)] if ci else None
    return dict(sorted(lanes.items()))


def _mark_comparability(cells: list[dict]) -> None:
    """Tag each pair of cells that differ in exactly one dimension.

    This is the honesty mechanism: if no two cells differ in exactly one dimension,
    nothing on the page can be read as a message result, and the table says so
    instead of inviting the comparison anyway.
    """
    for a in cells:
        for b in cells:
            if a is b:
                continue
            diffs = [d for d in ("overlay", "segment", "seat", "variant") if a[d] != b[d]]
            if len(diffs) == 1:
                entry = f"{diffs[0]} vs {b['cell_id']}"
                if entry not in a["comparable_on"]:
                    a["comparable_on"].append(entry)


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Learning cells for a profile's sequences.")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--content-root", default=None)
    ap.add_argument("--format", choices=("json", "text"), default="json")
    ap.add_argument(
        "--baseline",
        type=float,
        default=0.059,
        help="reply-rate baseline the detectable-lift column is read against",
    )
    args = ap.parse_args(argv)
    root = Path(args.content_root) if args.content_root else None

    from gtm_core.outcomes import read_outcomes
    from gtm_core.paths import resolve_content_root

    rows = read_outcomes(root or resolve_content_root(), args.profile)
    model = build_cells(args.profile, root, outcome_rows=rows, baseline=args.baseline)

    if args.format == "json":
        print(json.dumps(model, indent=2))
        return 0

    if not model["cells"]:
        print("no cells — is sequences/cells.toml present?")
        return 1
    print(f"{'cell':52} {'enrol':>6} {'send':>6} {'sent':>6} {'repl':>5} {'rate':>7}  lift")
    for c in model["cells"]:
        rate = "—" if c["reply_rate"] is None else f"{c['reply_rate']:.1%}"
        lift = "—" if c["detectable_lift"] is None else f"{c['detectable_lift']}x"
        print(
            f"{c['cell_id']:52} {c['enrolled']:>6} {c['sendable']:>6} "
            f"{int(c['sent']):>6} {int(c['replied']):>5} {rate:>7}  {lift}"
        )
    if model["confounded"]:
        print("\n⚠️  no two cells differ in exactly one dimension — nothing here is comparable")
    if model["by_lane"]:
        print(f"\n{'lane':14} {'enrol':>6} {'sent':>6} {'repl':>5} {'rate':>7}  ci95")
        for b in model["by_lane"].values():
            rate = "—" if b["reply_rate"] is None else f"{b['reply_rate']:.1%}"
            ci = "—" if not b["ci95"] else f"{b['ci95'][0]:.1%}–{b['ci95'][1]:.1%}"
            print(
                f"{b['lane']:14} {b['enrolled']:>6} {int(b['sent']):>6} {int(b['replied']):>5} {rate:>7}  {ci}"
            )
        if all(b["sent"] == 0 for b in model["by_lane"].values()):
            print(
                "  no `sent` rows on file — run `python -m gtm_core.sequencer_sends` before reading rates"
            )
    for o in model["overlaps"]:
        print(
            f"\n⚠️  {o['emails']} address(es) enrolled in both {o['first']} and {o['also_in']} — replies attribute to the first"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())


#: Coarse classification of a "why now" clause, so a reader can see WHAT KIND of reason
#: each email opens on rather than being told they all carry one. Keyword-matched and
#: deliberately coarse — the split that matters is *event* (something happened on a date)
#: vs *capability* (a durable description of what the company does), because only the
#: first decays and only the first justifies "why now".
_SIGNAL_KINDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("funding", "event", ("raised", "raises", "funding", "seed round", "series ", "secures $")),
    ("leadership", "event", ("appoints", "appointed", "names ", "hires", "joins as")),
    ("partnership", "event", ("partners", "partnership", "teams up", "collaborat")),
    ("m&a", "event", ("acquires", "acquired", "acquisition", "merges")),
    ("certification", "event", ("iso ", "soc 2", "certified", "certification", "accredit")),
    ("expansion", "event", ("expands", "opens ", "launches in", "enters the")),
    ("launch", "event", ("launch", "ships", "releases", "unveils", "rolls out", "introduc")),
)


def classify_signal(clause: str) -> tuple[str, str]:
    """``(kind, shape)`` for one why-now clause. ``shape`` is ``event`` or ``capability``.

    A capability clause ("X runs an agent that screens resumes") is durable and true
    next quarter; an event clause ("X raised a Series B") decays. Both are legitimate
    openers, but they are not the same claim, and a campaign that describes itself as
    event-led should be able to see which it actually shipped.
    """
    low = (clause or "").strip().lower()
    if not low:
        return ("none", "none")
    for kind, shape, cues in _SIGNAL_KINDS:
        if any(c in low for c in cues):
            return (kind, shape)
    return ("capability", "capability")


def supply_profile(profile: str, content_root: Path | None = None) -> dict:
    """Who is enrolled, cut the ways a reader actually asks: by country, by seat, and by
    the kind of reason each email opens on.

    Also returns the titles the seat resolver could not place. That list is the answer to
    "why are so many unknown?", and it is usually a *resolver* gap rather than a data gap:
    the linter's cue lists cover fewer seats than a tenant's own voice guide defines, so a
    perfectly ordinary title lands in "unknown" and the persona gate then stays silent on it.
    """
    base = _prospects_dir(profile, content_root) / "sequences"
    countries: dict[str, int] = {}
    fits: dict[str, int] = {}
    supp = 0
    q_sendable = 0
    supp_reasons: dict[str, int] = {}
    seats: dict[str, int] = {}
    unplaced: dict[str, int] = {}
    signals: dict[str, dict] = {}
    total = 0

    for src in load_cell_map(profile, content_root):
        csv_path = base / _safe_segment(src["csv"], "csv")
        if not csv_path.is_file():
            continue
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                total += 1
                country = (row.get("country") or "unknown").strip() or "unknown"
                countries[country] = countries.get(country, 0) + 1
                title = (row.get("title") or "").strip()
                seat = seat_of(title)
                seats[seat or UNKNOWN] = seats.get(seat or UNKNOWN, 0) + 1
                if not seat and title:
                    unplaced[title] = unplaced.get(title, 0) + 1
                suppressed = bool((row.get("suppression") or "").strip())
                if suppressed:
                    supp += 1
                    supp_reasons[(row.get("suppression") or "").strip().split(":")[0]] = (
                        supp_reasons.get((row.get("suppression") or "").strip().split(":")[0], 0)
                        + 1
                    )
                fit = role_fit(title)
                fits[fit] = fits.get(fit, 0) + 1
                if not suppressed and fit == "in":
                    q_sendable += 1
                kind, shape = classify_signal(row.get("signal_clause") or "")
                bucket = signals.setdefault(kind, {"kind": kind, "shape": shape, "n": 0})
                bucket["n"] += 1

    def _sorted(d):
        return [{"name": k, "n": v} for k, v in sorted(d.items(), key=lambda kv: -kv[1])]

    sig = sorted(signals.values(), key=lambda s: -s["n"])
    return {
        "total": total,
        "countries": _sorted(countries),
        "seats": _sorted(seats),
        "unplaced_titles": _sorted(unplaced)[:25],
        "unplaced_total": sum(unplaced.values()),
        "unplaced_distinct": len(unplaced),
        "signals": sig,
        # Role fit from the repo's own pre-enrolment gate. "unclear" is NOT a soft pass —
        # it means nobody has read that title yet — so targets are stated on "in" only.
        "qualified": fits.get("in", 0),
        # The number that actually matters: role-fit AND not suppressed. Targets are set
        # on this. Reporting only "qualified" credits the campaign with people a later
        # signal-quality pass already ruled unmailable.
        "qualified_sendable": q_sendable,
        "suppressed": supp,
        "sendable": total - supp,
        "suppression_reasons": [
            {"reason": k, "n": v} for k, v in sorted(supp_reasons.items(), key=lambda kv: -kv[1])
        ],
        "unclear": fits.get("unclear", 0),
        "not_qualified": fits.get("out", 0),
        "event_shaped": sum(s["n"] for s in sig if s["shape"] == "event"),
        "capability_shaped": sum(s["n"] for s in sig if s["shape"] == "capability"),
    }


#: Buyer-intent feeds this pipeline can source, and whether a feed is company-level or
#: person-level. Listing the full roster — not only what happens to be present — is the
#: point: a reader cannot tell "we have no hiring signal on this list" from "hiring signal
#: does not exist as a concept here" unless both are shown.
INTENT_FEEDS = (
    ("vibe-topic", "Vibe / Bombora topic intent", "company", "surging research on a topic"),
    (
        "rr-intentsify",
        "RocketReach / Intentsify topic intent",
        "company",
        "surging research on a topic",
    ),
    ("rr-news", "RocketReach news signal", "company", "a published company event"),
    ("rr-hiring", "RocketReach hiring signal", "company", "roles being opened"),
    ("job-change", "Job-change timing (new in role)", "person", "recently started the job"),
)


def intent_profile(profile: str, content_root: Path | None = None) -> dict:
    """What buyer-intent evidence stands behind the enrolled list, and for how much of it.

    The enrolment CSVs carry no intent columns, so this joins each enrolled person back to
    the prospect pool (``prospects/latest.json``). Two properties make the result honest
    rather than flattering:

    * **Coverage is reported, not assumed.** A person who cannot be traced back to a pool
      record has *no* known ICP score or intent, and is counted as such instead of being
      dropped from the denominator.
    * **The join is company-level.** Enrolled addresses almost never match the pool's own
      contact address, so the fallback is the company domain. Topic intent is a
      company-level measure anyway, so that is sound for intent and score — but it means
      an intent reading describes the person's *employer*, never the person.
    """
    import json as _json

    base = _prospects_dir(profile, content_root)
    pool_path = base / "latest.json"
    pool: list[dict] = []
    if pool_path.is_file():
        try:
            doc = _json.loads(pool_path.read_text(encoding="utf-8"))
            pool = doc.get("items", []) if isinstance(doc, dict) else (doc or [])
        except (OSError, ValueError):
            pool = []

    by_email: dict[str, dict] = {}
    by_domain: dict[str, dict] = {}
    for it in pool:
        if not isinstance(it, dict):
            continue
        e = str(it.get("contact_email") or "").strip().lower()
        if e:
            by_email.setdefault(e, it)
        d = str(it.get("domain") or "").strip().lower()
        if d:
            by_domain.setdefault(d, it)

    seq_dir = base / "sequences"
    total = matched = 0
    scores: list[float] = []
    feeds: dict[str, int] = {}
    topics: dict[str, dict] = {}
    paths: dict[str, int] = {}
    new_in_role = {"true": 0, "false": 0, "unknown": 0}

    for src in load_cell_map(profile, content_root):
        csv_path = seq_dir / _safe_segment(src["csv"], "csv")
        if not csv_path.is_file():
            continue
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                total += 1
                email = (row.get("email") or "").strip().lower()
                rec = by_email.get(email) or by_domain.get(
                    (row.get("company_domain") or "").strip().lower()
                )
                if not rec:
                    continue
                matched += 1
                if isinstance(rec.get("score"), (int, float)):
                    scores.append(float(rec["score"]))
                raw = rec.get("intent_feeds")
                for f in raw if isinstance(raw, list) else ([raw] if raw else []):
                    feeds[str(f)] = feeds.get(str(f), 0) + 1
                raw = rec.get("intent_topics")
                for t in raw if isinstance(raw, list) else ([raw] if raw else []):
                    name = str(t.get("topic") if isinstance(t, dict) else t).strip()
                    if not name:
                        continue
                    b = topics.setdefault(name, {"topic": name, "n": 0, "scores": []})
                    b["n"] += 1
                    if isinstance(t, dict) and isinstance(t.get("score"), (int, float)):
                        b["scores"].append(float(t["score"]))
                p = str(rec.get("qualification_path") or "unrecorded")
                paths[p] = paths.get(p, 0) + 1
                nir = rec.get("new_in_role")
                new_in_role["unknown" if nir is None else ("true" if nir else "false")] += 1

    for b in topics.values():
        b["avg_score"] = round(sum(b["scores"]) / len(b["scores"]), 1) if b["scores"] else None
        b.pop("scores")

    present = set(feeds)
    return {
        "total": total,
        "matched": matched,
        "unmatched": total - matched,
        "score_n": len(scores),
        "score_min": min(scores) if scores else None,
        "score_max": max(scores) if scores else None,
        "score_avg": round(sum(scores) / len(scores), 1) if scores else None,
        "feeds": [
            {
                "key": k,
                "label": label,
                "level": level,
                "means": means,
                "n": feeds.get(k, 0),
                "present": k in present,
            }
            for k, label, level, means in INTENT_FEEDS
        ],
        "topics": sorted(topics.values(), key=lambda t: -t["n"])[:12],
        "paths": [{"name": k, "n": v} for k, v in sorted(paths.items(), key=lambda kv: -kv[1])],
        "new_in_role": new_in_role,
    }
