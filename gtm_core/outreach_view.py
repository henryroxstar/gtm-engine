"""Turn the classified view into the log's three files — and say whether they are still current.

This is the only module of the outreach log that writes. It replaces exactly three derived files
in ``content/<profile>/prospects/`` — ``outreach-log.csv``, ``outreach-log.md`` and the stamp
``outreach-log.stamp.json`` — and nothing else. They hold nothing that cannot be rebuilt, so
losing them loses nothing.

Order of work: everything is computed first (no lock, no write); only then is the folder's lock
taken, the CSV and the ``.md`` replaced, and the stamp written LAST. A crash after the CSV but
before the stamp leaves a stamp that does not match the CSV, which ``status`` reads as stale.
The lock is the one the ``latest.json`` writers use (blocking, no timeout), so it is held for
the three writes only. ``--dry-run`` takes no lock and writes nothing.

The CSV is built as a string and written with :func:`gtm_core.fsio.atomic_write_text`; the
shared CSV helper blanks a literal ``0`` (``row.get(c) or ""``) and this log carries counts.
Any cell that starts like a spreadsheet formula gets a leading apostrophe, on every column.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import UTC, datetime
from pathlib import Path

from . import outreach_strict as strict
from .fsio import atomic_write_json, atomic_write_text
from .outreach_model import COLUMNS, KIND_CONTACTED, KIND_PACK, KIND_SEQUENCE, View, state_words
from .outreach_strict import StrictInputError
from .prospect_paths import prospects_dir
from .prospects_lock import ledger_lock

STAMP_FORMAT = 1
CSV_NAME, MD_NAME, STAMP_NAME = "outreach-log.csv", "outreach-log.md", "outreach-log.stamp.json"
#: A warning (never a refusal) when the CSV grows past this: a spreadsheet struggles first.
SIZE_WARN_BYTES = 8 * 1024 * 1024
#: The `.md` shows this many people per sequence; the rest are in the CSV.
MD_PEOPLE_CAP = 12
MD_PACK_CAP = 100
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
_BUILD_ID = re.compile(r"^Build id: (\S+)", re.M)


def guard(value: object) -> str:
    """A cell that can never execute as a formula. Applied to every column."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_LEAD) else text


def csv_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=COLUMNS, restval="", extrasaction="raise", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({c: guard(row.get(c, "")) for c in COLUMNS})
    return buf.getvalue()


def _esc(value: object) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\n", " ").strip()


def _age_phrase(fetched: str | None, now: datetime) -> str:
    if not fetched:
        return "Saleshandy figures: none yet"
    from .email_campaign_dashboard.config import FIGURES_MAX_AGE_DAYS
    from .email_campaign_dashboard.health import figures_age_days

    days = figures_age_days(fetched, now)
    if days is None:
        return f"Saleshandy figures dated {_esc(fetched)[:30]}, which cannot be trusted as a date"
    plural = "day" if days == 1 else "days"
    over = (
        f" — older than the {FIGURES_MAX_AGE_DAYS}-day limit the page trusts"
        if days > FIGURES_MAX_AGE_DAYS
        else ""
    )
    return f"Saleshandy figures of {fetched[:10]} ({days} {plural} old{over})"


def counts_line(view: View) -> str:
    kinds = [r["kind"] for r in view.rows]
    return (
        f"{view.people} people in sequences ({kinds.count(KIND_SEQUENCE)} rows), "
        f"{kinds.count(KIND_PACK)} drafted packs, "
        f"{kinds.count(KIND_CONTACTED)} contacted with no sequence"
    )


def sequence_check(f) -> str:
    """One plain-words line saying how our list compares with the provider's total."""
    notes: list[str] = []
    if f.provider_total is not None:
        if f.provider_total > f.people:
            notes.append(
                f"Saleshandy holds {f.provider_total - f.people} more people than any record names"
            )
        elif f.people > f.provider_total:
            notes.append(
                f"our records list {f.people - f.provider_total} more than Saleshandy shows"
            )
    if f.missing_lists:
        notes.append("list file missing: " + ", ".join(_esc(m) for m in f.missing_lists))
    return "; ".join(notes) or "agrees"


def summary_lines(view: View, now: datetime) -> list[str]:
    """What the operator reads in the terminal: counts, the figures' age, every note and every
    finding in full, and how many sequences could not be checked."""
    out = [f"Outreach log: {counts_line(view)}.", _age_phrase(view.snapshot_fetched, now) + "."]
    out += [f"Note: {n}" for n in view.notes]
    if view.suppressed_by_reason:
        out.append(
            f"{sum(view.suppressed_by_reason.values())} people are suppressed (do not contact)."
        )
    out += [f"FINDING {f.code}: {f.message}" for f in view.findings]
    if view.not_checkable:
        out.append(
            f"{view.not_checkable} sequence(s) could not be checked for sends that were never "
            "written down (no recent Saleshandy figures for them)."
        )
    return out


def md_text(view: View, profile: str, build_id: str, built_at: str, now: datetime) -> str:
    words = state_words()
    lines = [
        f"# Outreach Log — {profile}",
        "",
        f"Build id: {build_id} · built {built_at}",
        _age_phrase(view.snapshot_fetched, now),
        counts_line(view),
        "",
        "The full list, with each person's email text, is `outreach-log.csv` next to this file.",
        "",
    ]
    lines += [f"- {_esc(n)}" for n in view.notes]
    lines += [f"- **{f.code}** — {_esc(f.message)}" for f in view.findings]
    if view.not_checkable:
        lines.append(
            f"- {view.not_checkable} sequence(s) could not be checked (no recent figures)."
        )
    lines += [
        "",
        "## Sequences",
        "",
        "| Sequence | State | Our people | Saleshandy | Emailed | Closed out | Check |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in view.sequences:
        total = "?" if f.provider_total is None else f.provider_total
        sent = "?" if f.emailed_shown is None else f.emailed_shown
        lines.append(
            f"| {_esc(f.name)} | {words[f.state]} | {f.people} | {total} | {sent} | {f.closed_out} | {_esc(sequence_check(f))} |"
        )
    lines += ["", "## People", ""]
    for f in view.sequences:
        people = [r for r in view.rows if r["kind"] == KIND_SEQUENCE and r["sequence_id"] == f.id]
        if not people:
            continue
        lines += [
            f"### {_esc(f.name)} — {words[f.state]} ({len(people)})",
            "",
            "| Status | Person | Company | Emailed | First email |",
            "|---|---|---|---|---|",
        ]
        for r in people[:MD_PEOPLE_CAP]:
            lines.append(
                f"| {_esc(r['person_status'])} | {_esc(r['persona_name'])} | {_esc(r['account'])} | {_esc(r['emailed'])} | {_esc(r['touch_1_subject'])} |"
            )
        if len(people) > MD_PEOPLE_CAP:
            lines.append(f"\n{len(people) - MD_PEOPLE_CAP} more in the CSV.")
        lines.append("")
    contacted = [r for r in view.rows if r["kind"] == KIND_CONTACTED]
    if contacted:
        lines += [
            f"## Contacted, in no sequence ({len(contacted)})",
            "",
            "| Outcome | Person | Company |",
            "|---|---|---|",
        ]
        lines += [
            f"| {_esc(r['outcome'])} | {_esc(r['persona_name'])} | {_esc(r['account'])} |"
            for r in contacted[:MD_PEOPLE_CAP]
        ]
        if len(contacted) > MD_PEOPLE_CAP:
            lines.append(f"\n{len(contacted) - MD_PEOPLE_CAP} more in the CSV.")
        lines.append("")
    packs = [r for r in view.rows if r["kind"] == KIND_PACK]
    lines += [
        f"## Drafted packs ({len(packs)})",
        "",
        "| Date | Account | Tier | Persona | Email | Subject | Channels | File |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in packs[:MD_PACK_CAP]:
        persona = _esc(r["persona_name"]) + (
            f" ({_esc(r['persona_title'])})" if r["persona_title"] else ""
        )
        lines.append(
            f"| {_esc(r['draft_date'])} | {_esc(r['account'])} | {_esc(r['tier'])} | {persona} | {_esc(r['email_address'])} | {_esc(r['email_subject'])} | {_esc(r['channels'])} | `{_esc(r['file'])}` |"
        )
    if len(packs) > MD_PACK_CAP:
        lines.append(f"\n{len(packs) - MD_PACK_CAP} more in the CSV.")
    return "\n".join(lines) + "\n"


def stamp_doc(build_id: str, built_at: str, snapshot_fetched: str | None, csv_body: str) -> dict:
    return {
        "format": STAMP_FORMAT,
        "build_id": build_id,
        "built_at": built_at,
        "snapshot_fetched": snapshot_fetched,
        "csv_sha256": hashlib.sha256(csv_body.encode("utf-8")).hexdigest(),
    }


def final_line(code: int, view: View | None, error: StrictInputError | None = None) -> str:
    if code == 3 and error is not None:
        return f"Did not write the outreach log: {error.path}: {error.reason}. The previous log is untouched."
    if code == 1 and view is not None:
        bits = []
        if view.problems:
            bits.append(
                f"{len(view.problems)} email text(s) could not be filled in for sequences that are sending, paused or not started"
            )
        if not view.snapshot_present:
            bits.append("the Saleshandy figures are missing")
        return "Wrote the outreach log, but " + " and ".join(bits) + "."
    return "Wrote the outreach log."


def build(
    profile: str, content_root: Path, *, dry_run: bool = False, now: datetime | None = None
) -> tuple[int, list[str]]:
    """Build the log. ``(exit code, lines to print)``; the last line says what the code means."""
    from .outreach_people import build_view

    now = now or datetime.now(UTC)
    try:
        view = build_view(profile, content_root, now=now)
    except StrictInputError as exc:
        return 3, [final_line(3, None, exc)]
    built_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    csv_body = csv_text(view.rows)
    build_id = hashlib.sha256((built_at + csv_body).encode("utf-8")).hexdigest()[:12]
    lines = summary_lines(view, now)
    if len(csv_body.encode("utf-8")) > SIZE_WARN_BYTES:
        lines.append(
            f"Note: the CSV is {len(csv_body) // (1024 * 1024)} MB; a spreadsheet may struggle to open it."
        )
    code = 1 if (view.problems or not view.snapshot_present) else 0
    if dry_run:
        return code, [*lines, "Dry run: nothing was written."]
    out = prospects_dir(profile, content_root)
    stamp = stamp_doc(build_id, built_at, view.snapshot_fetched, csv_body)
    with ledger_lock(out / CSV_NAME, create=True):
        atomic_write_text(out / CSV_NAME, csv_body)
        atomic_write_text(out / MD_NAME, md_text(view, profile, build_id, built_at, now))
        atomic_write_json(out / STAMP_NAME, stamp)  # LAST: a crash before this reads as stale
    return code, [*lines, final_line(code, view)]


# ── is the log still current? ─────────────────────────────────────────────────────────────


def _instant(text: object) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        when = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def status(profile: str, content_root: Path) -> tuple[int, str]:
    """0 = current; 1 = stale or missing (the sentence says why); 3 = an input is unreadable.

    Current means: the stamp, the ``.md`` header and the CSV agree with each other, the stamp
    names the Saleshandy figures that exist NOW, and the build began no earlier than them."""
    import json

    out = prospects_dir(profile, content_root)
    try:
        snap = strict.read_snapshot(profile, content_root)
    except StrictInputError as exc:
        return 3, f"Cannot tell: {exc.path}: {exc.reason}."
    if not all((out / n).is_file() for n in (CSV_NAME, MD_NAME, STAMP_NAME)):
        return 1, "No outreach log yet, or part of it is missing — run build."
    try:
        stamp = json.loads((out / STAMP_NAME).read_text(encoding="utf-8"))
        csv_sha = hashlib.sha256((out / CSV_NAME).read_bytes()).hexdigest()
        md = (out / MD_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return 1, "The log's stamp or files cannot be read — run build."
    if not isinstance(stamp, dict) or stamp.get("format") != STAMP_FORMAT:
        return 1, "The log's stamp is not one this version wrote — run build."
    found = _BUILD_ID.search(md)
    if stamp.get("csv_sha256") != csv_sha or not found or found.group(1) != stamp.get("build_id"):
        return 1, "The log's files do not match its stamp (a build was interrupted) — run build."
    if stamp.get("snapshot_fetched") != snap.fetched:
        return 1, "The Saleshandy figures have changed since the log was built — run build."
    built, fetched = _instant(stamp.get("built_at")), _instant(snap.fetched)
    if built is None or (fetched is not None and built < fetched):
        return 1, "The log was built before the Saleshandy figures it should reflect — run build."
    return 0, "The outreach log is current."
