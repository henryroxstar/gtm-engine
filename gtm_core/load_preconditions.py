"""What must be on record before anyone is loaded into a sequence.

Loading people into a sequencer is the one step here that cannot be taken back (their details
leave for a third party and, once someone presses Start, so do the emails). Until now the checks
around it lived in whoever was driving: the compliance preflight was a command somebody had to
choose to run, its result was written down only when they remembered a flag, and nothing stopped
a load that ignored it. A load on 2026-09-26 went ahead on a failed preflight for exactly that
reason, and left no history row either.

This module is the one place that answers "may this load go ahead?" from the profile's own
records, so the approved dispatcher (``agent/email_dispatch.py``) and the Claude Code hook
(``.claude/hooks/enrol-hook.py``) cannot disagree:

* **A compliance check must be on record, and not failed**, for the exact sequence
  (``capability_asserted``, written by ``gtm_core.email_compliance preflight``).
* **The copy must be the copy that was checked**: when staging recorded a digest of the steps
  (``step_sha256`` with ``step_sha256_algo``), the copy about to be loaded must match it.
* **A pilot is a ceiling**: a campaign that declares a ``pilot_size`` may not grow past it until
  a ``pilot_read_ok`` event says its first results were read.

Every rule refuses rather than skips: an unreadable history, an unreadable ``cells.toml`` or a
malformed field is a refusal, because a smaller answer from unreadable input reads exactly like
a clean one. Every refusal is a :class:`~gtm_core.refusal_copy.Refusal`, so it says what was
stopped, why, and what the operator can do, in the same plain words as every other stop.

Stdlib only and importable on the system Python a hook may be run with (``tomllib`` is imported
inside the one function that reads ``cells.toml``). Reads only; writes nothing.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from gtm_core.copy_words import COPY_DIGEST_ALGO, copy_digests
from gtm_core.paths import _safe_segment, resolve_content_root
from gtm_core.refusal_copy import Refusal

PREFLIGHT_EVENT = "capability_asserted"
STAGED_EVENT = "sequence_staged"
PILOT_EVENT = "pilot_read_ok"

#: The only statuses that let a load proceed. A closed list: a missing, empty or unfamiliar word
#: refuses. ``WARN`` is a non-blocking concern the preflight itself reports as a pass.
GRANTING_STATUSES = frozenset({"PASS", "WARN"})

_HEX = frozenset("0123456789abcdef")

_NOTHING = "I haven't loaded anyone."


class HistoryUnreadable(Exception):
    """A line of ``history.jsonl`` could not be read; the whole read is refused."""


class CellsUnreadable(Exception):
    """``cells.toml`` or a list it registers could not be read."""


# ── reading ───────────────────────────────────────────────────────────────────


def history_path(content_root: Path | str | None, profile: str) -> Path:
    root = Path(content_root) if content_root is not None else resolve_content_root()
    return root / _safe_segment(profile, "profile") / "history.jsonl"


def read_history(content_root: Path | str | None, profile: str) -> list[dict]:
    """Every row of the profile's ``history.jsonl``, oldest first.

    Unlike ``Ledgers.iter_history``, which skips a corrupt line so a status page can still
    render, this REFUSES: a skipped line could be the very row (a failed preflight) the caller is
    looking for. A missing file is an empty history. An unsafe profile raises ``ValueError``.
    """
    path = history_path(content_root, profile)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise HistoryUnreadable(f"the file cannot be read ({type(exc).__name__})") from None
    rows: list[dict] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise HistoryUnreadable(f"line {number} is not valid JSON") from None
        if not isinstance(row, dict):
            raise HistoryUnreadable(f"line {number} is not a JSON object")
        rows.append(row)
    return rows


def read_cells(content_root: Path | str | None, profile: str) -> list[dict]:
    """The raw ``[[sequence]]`` entries of ``sequences/cells.toml``; ``[]`` if the file is absent.

    Unlike ``gtm_core.cells.load_cell_map`` (fail-soft, and it drops keys it does not know) this
    keeps every key and REFUSES a file it cannot parse.
    """
    import tomllib  # 3.11+; deferred so the history-only rules import on an older system Python

    path = _sequences_dir(content_root, profile) / "cells.toml"
    if not path.is_file():
        return []
    try:
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CellsUnreadable(f"cells.toml cannot be read ({type(exc).__name__})") from None
    entries = doc.get("sequence", [])
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise CellsUnreadable("cells.toml has a [[sequence]] entry that is not a table")
    return entries


def _sequences_dir(content_root: Path | str | None, profile: str) -> Path:
    root = Path(content_root) if content_root is not None else resolve_content_root()
    return root / _safe_segment(profile, "profile") / "prospects" / "sequences"


def _word(value: Any) -> str:
    return value.strip().upper() if isinstance(value, str) else ""


def _one_line(text: Any, limit: int = 160) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# ── the compliance check ──────────────────────────────────────────────────────

_PREFLIGHT_COMMAND = (
    "uv run python -m gtm_core.email_compliance preflight --profile <profile> "
    "--sequence-id {sid} --step-id <each step id> --settings-json <get_sequence_settings> "
    "--accounts-json <list_email_accounts> --leads-csv <the list>"
)


def latest_preflight(rows: list[dict], sequence_id: Any) -> dict | None:
    """The newest ``capability_asserted`` row for exactly this sequence, or None.

    File order, because the ledger is append-only. A row written without a sequence id binds to
    no sequence and never matches one.
    """
    if not isinstance(sequence_id, str) or not sequence_id.strip():
        return None
    found = None
    for row in rows:
        if row.get("event") == PREFLIGHT_EVENT and row.get("sequence_id") == sequence_id:
            found = row
    return found


def sequences_for_step(rows: list[dict], step_id: Any) -> set[str]:
    """The sequences whose recorded checks list this step id.

    The hosted connector's import names a STEP, not a sequence, so the only way to know which
    sequence it loads is the step ids a check or a staging row recorded. More than one answer is
    ambiguous and the caller must not pick.
    """
    out: set[str] = set()
    if not isinstance(step_id, str) or not step_id.strip():
        return out
    for row in rows:
        if row.get("event") not in (PREFLIGHT_EVENT, STAGED_EVENT):
            continue
        ids = row.get("step_ids")
        sid = row.get("sequence_id")
        if (
            isinstance(ids, list)
            and ids
            and all(isinstance(i, str) for i in ids)
            and step_id in ids
            and isinstance(sid, str)
            and sid.strip()
        ):
            out.add(sid)
    return out


def preflight_refusal(rows: list[dict], sequence_id: Any) -> Refusal | None:
    """Refuse unless the sequence's latest compliance check is on record and did not fail."""
    row = latest_preflight(rows, sequence_id)
    sid = sequence_id if isinstance(sequence_id, str) and sequence_id.strip() else "(none named)"
    if row is None:
        return Refusal(
            what=_NOTHING,
            why=(
                f"no compliance check is on record for sequence {sid}, so nobody has confirmed "
                "its unsubscribe link, postal address and target markets"
            ),
            next_step="run the compliance check for this sequence with its id, then approve again",
            technical=_PREFLIGHT_COMMAND.format(sid=sid),
        )
    status = _word(row.get("status"))
    overall_given = "overall" in row
    overall = _word(row.get("overall"))
    if status in GRANTING_STATUSES and (not overall_given or overall in GRANTING_STATUSES):
        return None
    names = row.get("failed_checks")
    if isinstance(names, list) and names and all(isinstance(n, str) for n in names):
        what = ", ".join(names)
    else:
        detail = row.get("detail")
        lines = [d for d in detail if isinstance(d, str)] if isinstance(detail, list) else []
        what = _one_line(
            next((d for d in lines if "FAIL" in d), lines[0] if lines else "no detail")
        )
    return Refusal(
        what=_NOTHING,
        why=f"the last compliance check for sequence {sid} failed ({what})",
        next_step="fix what it flagged, run the check again, and approve again",
        technical=_PREFLIGHT_COMMAND.format(sid=sid),
    )


# ── the copy that was checked ─────────────────────────────────────────────────


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 16 and set(value) <= _HEX


def staged_copy_refusal(rows: list[dict], sequence_id: Any, steps: Any) -> Refusal | None:
    """Refuse when the copy about to load is not the copy digested when the sequence was staged.

    Only a digest written by THIS algorithm is compared (``step_sha256_algo``). One written
    before it existed, by hand and by an unknown method, cannot be compared and is not a
    difference. But a row that claims this algorithm and carries an unreadable list is a broken
    record, and refuses.
    """
    staged = None
    for row in rows:
        if row.get("event") == STAGED_EVENT and row.get("sequence_id") == sequence_id:
            staged = row
    if staged is None or staged.get("step_sha256_algo") != COPY_DIGEST_ALGO:
        return None
    recorded = staged.get("step_sha256")
    if not isinstance(recorded, list) or not recorded or not all(_is_digest(d) for d in recorded):
        return Refusal(
            what=_NOTHING,
            why=(
                f"the record of sequence {sequence_id}'s copy from staging is damaged, so I cannot "
                "tell whether the copy changed since it was checked"
            ),
            next_step="stage the sequence again so a fresh record is written, then approve again",
        )
    try:
        live = copy_digests(steps)
    except ValueError:
        return Refusal(
            what=_NOTHING,
            why="the approved list does not carry the sequence copy in a form I can check",
            next_step="rebuild the approved list from the review, then approve again",
        )
    if len(live) != len(recorded):
        return Refusal(
            what=_NOTHING,
            why=(
                f"the sequence now has {len(live)} steps and {len(recorded)} were on record "
                "when it was checked"
            ),
            next_step="check the copy again and stage it again so the new copy is on record",
        )
    for index in range(len(live)):  # lengths are equal here; no zip(strict=) on a 3.9 hook
        if live[index] != recorded[index]:
            return Refusal(
                what=_NOTHING,
                why=(
                    f"the copy in step {index + 1} is not the copy that was checked when "
                    f"sequence {sequence_id} was staged"
                ),
                next_step="check the copy again and stage it again so the new copy is on record",
            )
    return None


# ── the pilot ceiling ─────────────────────────────────────────────────────────


def _positive_whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _emails(draft: dict) -> set[str]:
    found: set[str] = set()
    rows = draft.get("prospect_list")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        for label, value in row.items():
            if str(label).strip().casefold() in ("email", "work email") and isinstance(value, str):
                if value.strip():
                    found.add(value.strip().casefold())
    return found


def _registered_emails(
    content_root: Path | str | None, profile: str, entries: list[dict]
) -> set[str]:
    found: set[str] = set()
    base = _sequences_dir(content_root, profile)
    for entry in entries:
        name = entry.get("csv")
        try:
            path = base / _safe_segment(name if isinstance(name, str) else "", "csv")
        except ValueError:
            raise CellsUnreadable(f"the list name {name!r} is not a plain file name") from None
        try:
            with path.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for record in reader:
                    for label, value in record.items():
                        if str(label).strip().casefold() == "email" and isinstance(value, str):
                            if value.strip():
                                found.add(value.strip().casefold())
        except (OSError, UnicodeDecodeError, csv.Error):
            raise CellsUnreadable(f"the registered list {name} cannot be read") from None
    return found


def _unit_of(
    draft: dict, entries: list[dict], rows: list[dict], sequence_id: str
) -> tuple[str, bool]:
    """(key, is_campaign): the campaign this load belongs to, else the sequence itself."""
    named = draft.get("campaign")
    if isinstance(named, str) and named.strip():
        return named.strip(), True
    for entry in entries:
        if entry.get("id") == sequence_id and str(entry.get("campaign") or "").strip():
            return str(entry["campaign"]).strip(), True
    staged = None
    for row in rows:
        if row.get("event") == STAGED_EVENT and row.get("sequence_id") == sequence_id:
            staged = row
    if staged and isinstance(staged.get("campaign"), str) and staged["campaign"].strip():
        return staged["campaign"].strip(), True
    return sequence_id, False


def _pilot_refusal_inner(
    content_root: Path | str | None, profile: str, draft: dict, rows: list[dict]
) -> Refusal | None:
    sequence_id = str(draft.get("sequence_id") or "")
    entries = read_cells(content_root, profile)
    unit, is_campaign = _unit_of(draft, entries, rows, sequence_id)
    members = [
        e
        for e in entries
        if (e.get("campaign") == unit if is_campaign else e.get("id") == unit)
        or e.get("id") == sequence_id
    ]
    declared: list[Any] = []
    if "pilot_size" in draft:
        declared.append(draft["pilot_size"])
    declared.extend(e["pilot_size"] for e in members if "pilot_size" in e)
    if not declared:
        return None
    if not all(_positive_whole(d) for d in declared):
        return Refusal(
            what=_NOTHING,
            why=f"the pilot size declared for {unit} is not a whole number of people, 1 or more",
            next_step="correct the pilot size, then approve again",
        )
    pilot = min(declared)
    if any(
        r.get("event") == PILOT_EVENT and unit in (r.get("campaign"), r.get("sequence_id"))
        for r in rows
    ):
        return None
    registered = _registered_emails(content_root, profile, [e for e in members if e.get("csv")])
    lead_ids = draft.get("lead_ids")
    extra = len(lead_ids) if isinstance(lead_ids, list) else 0
    total = len(registered | _emails(draft)) + extra
    if total <= pilot:
        return None
    return Refusal(
        what=_NOTHING,
        why=(
            f"{unit} is limited to a pilot of {pilot} people until its first results are read, "
            f"and this would take it to {total}"
        ),
        next_step=(
            f"trim the list to {pilot} people, or read the pilot's results and record the "
            f"go-ahead (a {PILOT_EVENT} entry for {unit})"
        ),
    )


def pilot_refusal(
    content_root: Path | str | None, profile: str, draft: dict, rows: list[dict]
) -> Refusal | None:
    """Refuse a load that would take a pilot-limited campaign past its ceiling.

    ``pilot_size`` is optional, on the approved list or on a registered ``[[sequence]]``; with
    none declared this returns None without counting anything. Where several are declared the
    smallest governs, so a list can tighten a campaign's pilot and never loosen it.
    """
    try:
        return _pilot_refusal_inner(content_root, profile, draft, rows)
    except CellsUnreadable as exc:
        return Refusal(
            what=_NOTHING,
            why=f"I cannot count the people already registered in this campaign ({exc})",
            next_step="repair the registered lists in cells.toml, then approve again",
        )


# ── the single entry point ────────────────────────────────────────────────────


def load_refusal(profile: Any, content_root: Path | str | None, draft: dict) -> Refusal | None:
    """The first reason this approved load must not go ahead, or None.

    Order: the compliance check, the copy that was checked, the pilot ceiling. Cheap and local:
    it reads the profile's own files and makes no network call.
    """
    if not isinstance(profile, str) or not profile.strip():
        return Refusal(
            what=_NOTHING,
            why="I cannot tell which profile this load belongs to, so I cannot check its records",
            next_step="start the run from a profile, then approve again",
        )
    sequence_id = draft.get("sequence_id")
    if not isinstance(sequence_id, str) or not sequence_id.strip():
        return Refusal(
            what=_NOTHING,
            why="the approved list does not say which sequence it goes to",
            next_step="rebuild the approved list from the review, then approve again",
        )
    try:
        rows = read_history(content_root, profile)
    except HistoryUnreadable as exc:
        return Refusal(
            what=_NOTHING,
            why=(
                f"the history file for this profile has a line I cannot read ({exc}), so I "
                "cannot tell whether the compliance check passed"
            ),
            next_step="repair that line (the ledger check shows where), then approve again",
        )
    except ValueError:
        return Refusal(
            what=_NOTHING,
            why="this profile's name is not a plain name, so I will not look for its records",
            next_step="start the run from a valid profile",
        )
    return (
        preflight_refusal(rows, sequence_id)
        or staged_copy_refusal(rows, sequence_id, draft.get("steps"))
        or pilot_refusal(content_root, profile, draft, rows)
    )
