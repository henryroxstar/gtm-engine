"""The strict readers the outreach log builds on: refuse a corrupt input, never read a smaller answer.

Routing's readers are deliberately lenient in one direction (a missing optional artifact is a
note, a bad line is skipped): :func:`gtm_core.cells.load_cell_map` returns ``[]`` for a corrupt
``cells.toml``, ``lanes.context._load_contacted`` reads with ``errors="ignore"``, and
``lanes.context._deleted_sequences`` skips an unparseable history line. That suits a router that
must keep going. It does not suit a LIST OF WHO WAS EMAILED, where a silently smaller answer
reads exactly like the right one. So this module reads the same files and raises
:class:`StrictInputError` — naming the file and why — and the build turns that into "did not
write, the old files are untouched". The two behaviours differ on purpose; a test holds both.

Pure stdlib plus path/format helpers. It imports nothing that writes, and it does not import
:mod:`gtm_core.cells` (that module puts the linter package on ``sys.path`` as a side effect).

Four inputs, one reader each:

* ``cells.toml`` — :func:`read_cells`: every registration, confined to the sequences directory;
  one id may be registered several times, but with ONE spec.
* ``contacted-*.csv`` — :func:`read_contacted`: both layouts that exist, merged by address with
  the strongest outcome winning.
* the Saleshandy figures — :func:`read_snapshot`: counters are ``None`` when ABSENT, never ``0``.
* ``history.jsonl`` — :func:`read_history_facts`: which sequences are recorded deleted, and
  which ids history knows.

``enrolments.jsonl`` (the dispatcher's own ledger) is read leniently by :func:`read_enrolments`,
like its other reader: an unreadable line is counted and reported, not fatal.
"""

from __future__ import annotations

import csv
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .enrolments import path_for as _enrolments_path
from .paths import _safe_segment
from .prospect_paths import pool_dir, sequences_dir
from .sequence_snapshot_format import FILE_NAME, figures_of, file_meta
from .sequencer_history import read_history
from .sequencer_stats_read import PayloadError, is_provider_id

#: The closed outcome vocabulary a closed-out person can carry. ``unclassified`` is a person
#: who WAS contacted whose file says nothing usable about what happened next — still closed out.
NO_REPLY = "no-reply"
REPLIED = "replied"
UNSUBSCRIBED = "unsubscribed"
BOUNCED = "bounced"
UNCLASSIFIED = "unclassified"
OUTCOMES = (NO_REPLY, REPLIED, UNSUBSCRIBED, BOUNCED, UNCLASSIFIED)
#: When two files disagree about a person, the strongest outcome wins.
_STRENGTH = {UNCLASSIFIED: 0, NO_REPLY: 1, REPLIED: 2, UNSUBSCRIBED: 3, BOUNCED: 4}

#: A sequence token holds ONE value today. A delimiter would mean a person is in several
#: sequences and the reader below would silently drop all but one — so it refuses instead.
_TOKEN_DELIMITERS = re.compile(r"[,;|\s]")
_YES = frozenset({"yes", "y", "true", "1"})
_NO = frozenset({"no", "n", "false", "0"})


class StrictInputError(Exception):
    """An input the log must not guess about. ``path`` and ``reason`` are for the operator."""

    def __init__(self, path: Path | str, reason: str):
        self.path = str(path)
        self.reason = reason
        super().__init__(f"{self.path}: {reason}")


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _count(value) -> int | None:
    """A whole non-negative number, from an int or a digit string. Anything else is ABSENT
    (``None``) — a counter that is not there is not a counter that is zero."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit() and len(value.strip()) <= 15:
        return int(value.strip())
    return None


# ── cells.toml ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Cells:
    present: bool
    registrations: list[dict]
    skipped: int  # rows missing id / csv / spec — routing drops these too; counted, not hidden


def _confined(seq_dir: Path, name: str, what: str, where: Path) -> None:
    """``name`` must stay inside the sequences directory once symlinks are resolved. A NUL
    byte makes ``resolve`` raise ``ValueError`` before any comparison, so it is caught here."""
    try:
        (seq_dir / name).resolve().relative_to(seq_dir.resolve())
    except ValueError as exc:
        raise StrictInputError(
            where, f"{what} {name!r} points outside the sequences folder"
        ) from exc


def read_cells(profile: str, content_root: Path) -> Cells:
    seq_dir = sequences_dir(profile, content_root)
    path = seq_dir / "cells.toml"
    if not path.is_file():
        return Cells(False, [], 0)
    try:
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise StrictInputError(path, f"cells.toml cannot be read ({type(exc).__name__})") from exc
    raw = doc.get("sequence", [])
    if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
        raise StrictInputError(path, "cells.toml [[sequence]] entries are not tables")
    out: list[dict] = []
    skipped = 0
    spec_of: dict[str, str] = {}
    for row in raw:
        if not (row.get("id") and row.get("csv") and row.get("spec")):
            skipped += 1
            continue
        sid, csv_name, spec = str(row["id"]), str(row["csv"]), str(row["spec"])
        _confined(seq_dir, csv_name, "list", path)
        _confined(seq_dir, spec, "spec", path)
        if spec_of.setdefault(sid, spec) != spec:
            # One sequence fed by several lists is normal; one sequence with two different
            # specs is two different emails under one name, and attribution would be a guess.
            raise StrictInputError(path, f"sequence {sid!r} is registered with two different specs")
        out.append(
            {
                "sequence_id": sid,
                "csv": csv_name,
                "spec": spec,
                "title": str(row.get("title", "") or ""),
                "campaign": str(row.get("campaign", "") or ""),
                "lane": str(row.get("lane", "") or "").strip().lower(),
            }
        )
    return Cells(True, out, skipped)


# ── history.jsonl ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HistoryFacts:
    deleted: frozenset[str]
    known: frozenset[str]


def read_history_facts(profile: str, content_root: Path) -> HistoryFacts:
    path = content_root / _safe_segment(profile, "profile") / "history.jsonl"
    try:
        rows = read_history(path)
    except PayloadError as exc:
        raise StrictInputError(path, str(exc)) from exc
    deleted: set[str] = set()
    known: set[str] = set()

    def add(into: set[str], value) -> None:
        text = _text(value) if isinstance(value, str) else ""
        if text:
            into.add(text)

    for row in rows:
        event = row.get("event")
        add(known, row.get("sequence_id"))
        for key in ("sequences", "new_sequences", "sequences_deleted"):
            for item in row.get(key) or []:
                if isinstance(item, dict):
                    add(known, item.get("id"))
        for sid in row.get("sequence_ids") or []:
            add(known, sid)
        # The two shapes lanes.context._deleted_sequences reads — read STRICTLY here.
        if event == "sequence_deleted":
            add(deleted, row.get("sequence_id"))
        elif event == "sequence_cleanup":
            for item in row.get("sequences_deleted") or []:
                if isinstance(item, dict):
                    add(deleted, item.get("id"))
    return HistoryFacts(frozenset(deleted), frozenset(known | deleted))


# ── contacted-*.csv ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Contact:
    email: str
    outcome: str
    tokens: tuple[str, ...]  # the sequence cell(s), verbatim; may be blank or a lane label
    sources: tuple[str, ...]  # file names
    name: str = ""  # the person's name as the file spells it, when it does
    domain: str = ""  # their company domain, when the file carries one


def _outcome_of(row: dict, cols: set[str]) -> str:
    if "outcome" in cols:
        word = _text(row.get("outcome")).lower()
        return word if word in OUTCOMES else UNCLASSIFIED
    flags = {k: _text(row.get(k)).lower() for k in (BOUNCED, UNSUBSCRIBED, REPLIED) if k in cols}
    if not flags:
        return UNCLASSIFIED  # a file with only an address: contacted, nothing else known
    for name in (BOUNCED, UNSUBSCRIBED, REPLIED):  # strongest first
        if flags.get(name) in _YES:
            return name
    if all(v in _NO for v in flags.values()) and len(flags) == 3:
        return NO_REPLY
    return UNCLASSIFIED


def read_contacted(profile: str, content_root: Path) -> dict[str, Contact]:
    """Every person in any ``contacted-*.csv``, merged by address, strongest outcome winning."""
    seq_dir = sequences_dir(profile, content_root)
    merged: dict[str, Contact] = {}
    for path in sorted(seq_dir.glob("contacted-*.csv")):
        _confined(seq_dir, path.name, "closed-out file", path)
        try:
            with path.open(newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                cols = set(reader.fieldnames or [])
                if "email" not in cols:
                    raise StrictInputError(path, "closed-out file has no 'email' column")
                for row in reader:
                    email = _text(row.get("email")).lower()
                    if not email:
                        continue
                    token = _text(
                        row.get("sequences") if "sequences" in cols else row.get("sequence")
                    )
                    if _TOKEN_DELIMITERS.search(token):
                        raise StrictInputError(
                            path, f"sequence cell {token!r} holds more than one value"
                        )
                    outcome = _outcome_of(row, cols)
                    name = _text(row.get("name")) or " ".join(
                        p for p in (_text(row.get("first")), _text(row.get("last"))) if p
                    )
                    domain = _text(row.get("company_domain")).lower()
                    prev = merged.get(email)
                    if prev is None:
                        merged[email] = Contact(
                            email, outcome, (token,), (path.name,), name, domain
                        )
                    else:
                        best = (
                            outcome
                            if _STRENGTH[outcome] > _STRENGTH[prev.outcome]
                            else prev.outcome
                        )
                        merged[email] = Contact(
                            email,
                            best,
                            prev.tokens + ((token,) if token not in prev.tokens else ()),
                            prev.sources + ((path.name,) if path.name not in prev.sources else ()),
                            prev.name or name,
                            prev.domain or domain,
                        )
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise StrictInputError(
                path, f"closed-out file cannot be read ({type(exc).__name__})"
            ) from exc
    return merged


def provider_token(token: str, known_ids: frozenset[str] | set[str]) -> bool:
    """Does this closed-out sequence cell name a REAL sequence? It must be provider-shaped AND
    already known (registered, in the snapshot, or in history): a ten-letter lane or segment
    label is provider-shaped too, and must not become a phantom sequence."""
    return bool(token) and is_provider_id(token) and token in known_ids


# ── the Saleshandy figures ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SnapRow:
    id: str
    name: str
    status: str | None  # the provider's own flag, lower-cased; None = the writer had no flag
    total: int | None  # people the provider holds; None = ABSENT, not zero
    contacted: int | None  # people emailed; None = ABSENT, not zero
    stamp: str | None  # that sequence's own fetch date


@dataclass(frozen=True)
class Snapshot:
    present: bool
    fetched: str | None
    rows: dict[str, SnapRow]
    edited: bool  # the file was changed by something other than the refresh command


def read_snapshot(profile: str, content_root: Path) -> Snapshot:
    path = pool_dir(profile, content_root) / FILE_NAME
    if not path.is_file():
        return Snapshot(False, None, {}, False)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise StrictInputError(
            path, f"the Saleshandy figures cannot be read ({type(exc).__name__})"
        ) from exc
    if isinstance(raw, dict) and isinstance(raw.get("sequences"), list):
        items, fetched = raw["sequences"], raw.get("fetched")
        stamps = raw.get("stamps") if isinstance(raw.get("stamps"), dict) else {}
        edited = bool(file_meta(raw).get("edited"))
    elif isinstance(raw, list):
        items, fetched, stamps, edited = raw, None, {}, False
    else:
        raise StrictInputError(path, "the Saleshandy figures file has an unexpected shape")
    fetched = fetched if isinstance(fetched, str) and fetched.strip() else None
    rows: dict[str, SnapRow] = {}
    for item in items:
        if not isinstance(item, dict):
            raise StrictInputError(path, "a sequence row in the figures file is not an object")
        sid = _text(item.get("sequenceId"))
        if not sid:
            raise StrictInputError(path, "a sequence row in the figures file has no sequenceId")
        if sid in rows:
            raise StrictInputError(path, f"sequence {sid!r} appears twice in the figures file")
        prospects = item.get("prospects")
        p0 = (
            prospects[0]
            if isinstance(prospects, list) and prospects and isinstance(prospects[0], dict)
            else {}
        )
        status = _text(item.get("status")).lower() or None
        stamp = stamps.get(sid) if isinstance(stamps.get(sid), str) else fetched
        rows[sid] = SnapRow(
            sid,
            _text(item.get("sequenceName")),
            status,
            _count(p0.get("total")),
            figures_of(item)["sent"],
            stamp,
        )
    return Snapshot(True, fetched, rows, edited)


# ── enrolments.jsonl (lenient, like its other reader) ─────────────────────────────────────


def read_enrolments(profile: str, content_root: Path) -> tuple[list[tuple[str, str]], int]:
    """``([(email, sequence_id)], unreadable_lines)`` from the dispatcher's own ledger."""
    path = _enrolments_path(content_root, profile)
    if not path.is_file():
        return [], 0
    out: list[tuple[str, str]] = []
    bad = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return [], 1
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        email = _text(row.get("email")).lower() if isinstance(row, dict) else ""
        sid = _text(row.get("sequence_id")) if isinstance(row, dict) else ""
        if email and sid:
            out.append((email, sid))
        else:
            bad += 1
    return out, bad


def read_list_strict(seq_dir: Path, sink: dict | None = None):
    """A ``read_list`` for :func:`gtm_core.lanes.context.load_enrolments_by_sequence`: strict
    UTF-8, the ``email`` column, trimmed and lower-cased, and a path that must stay confined.
    A list that is not UTF-8 refuses the build instead of dropping its unreadable bytes.

    ``sink`` (optional) receives ``{list file name: {email: first row}}`` so the caller can
    read names, companies and merge fields from the SAME pass instead of opening the list again.
    """

    def read(path: Path) -> tuple[str, ...]:
        try:
            path.resolve().relative_to(seq_dir.resolve())
        except ValueError as exc:
            raise StrictInputError(path, "list points outside the sequences folder") from exc
        out: list[str] = []
        rows: dict[str, dict] = {}
        try:
            with path.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    email = _text(row.get("email")).lower()
                    if email:
                        out.append(email)
                        rows.setdefault(email, row)
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise StrictInputError(path, f"list cannot be read ({type(exc).__name__})") from exc
        if sink is not None:
            sink[path.name] = rows
        return tuple(out)

    return read


# ── the suppression ledger: EVERY row, not the first ──────────────────────────────────────


def read_suppression(profile: str, content_root: Path) -> list:
    """Every row of the suppression ledger, strict UTF-8. An absent ledger is an empty one.

    ``LedgerIndex.match`` keeps only the FIRST entry per key (``setdefault``), so a harmless
    bookkeeping row ("loaded in another sequence") could shadow a real opt-out row for the same
    person. The log must see all of them, so it reads the entries itself.
    """
    from .prospect_paths import suppression_ledger
    from .suppression import _read_entries

    path = suppression_ledger(profile, content_root)
    try:
        return _read_entries(path)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise StrictInputError(
            path, f"the suppression ledger cannot be read ({type(exc).__name__})"
        ) from exc
