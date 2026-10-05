"""The one writer of ``.pool/sequence-stats.json`` — the sending figures the status page reads.

The skill used to rewrite that file by hand, as one file with one date, so refreshing a single
sequence either re-stamped every other sequence as fresh or dropped them. This writer does a
locked read-merge-write instead: each sequence carries the date *its own* figures were fetched,
so the page can say how old the oldest of them is.

    write   --profile P --payload FILE [--payload FILE …] [--fetched DATE] [--replace] [--prune]
    forget  --profile P --id ID
    ack     --profile P [--id ID]       clear a recorded "this counter fell" note
    status  --profile P                 list sequences whose figures are missing, old or undated

**One validator, stricter than the ledger's.** A payload is checked by
:func:`sequence_payload_check.check_payload` — shape, a usable text ``sequenceId`` on every row,
counters that are short whole numbers, a text name, nothing the file cannot hold — and then with
:func:`sequencer_sends.sequence_totals`, the totals reader the sends ledger trusts. What it accepts
is a shape ``sequencer_sends.sequences_in`` reads as the same rows; what it refuses, it refuses
by name rather than dropping a row and reporting success. The merged document is then run through
the status page's own row reader (:mod:`sequencer_snapshot_gate`), so a file the page could not
read is never written. Every check runs before the first byte is written, and the write itself is
atomic, so a refusal or a crash leaves the file byte-identical.

**The payload age gate is the payload file's age.** A file saved more than ``PAYLOAD_MAX_AGE_S``
ago, or modified ahead of this machine's clock by more than ``PAYLOAD_FUTURE_SKEW_S``, is refused.
That is a convenience guard against last week's export, not proof of when the figures inside were
fetched: ``touch`` moves a file's date and nothing here can see through it. Nothing in this module
proves a payload came from the sequencer. For the same reason a copy of an older file fed back in
is not refused when it is in the older shape; the success message names every row it re-dated, and
marks the ones whose figures are identical to the previous record, so a re-date is never silent.

**Everything printed is escaped.** A sequence id is untrusted text (it may be quoted from a file
nothing validated), and the lines are read by an agent and a terminal: ids go through
``printable``, and an id is only ever put inside a suggested command when it needs no escaping.

**What the recorded hashes mean.** ``payload_sha256`` and ``body_sha256`` are *recorded by the
refresh command*: they show a later reader that the file was changed by something other than this
command. They are not verification that the figures are true.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from gtm_core import sequencer_snapshot_gate as gate
from gtm_core.fsio import atomic_write_json
from gtm_core.page_inputs_io import printable
from gtm_core.paths import resolve_content_root
from gtm_core.prospects_lock import ledger_lock
from gtm_core.sequence_snapshot_format import (
    FALL_FIELDS,
    FILE_NAME,
    FORMAT,
    IDENTICAL,
    body_digest,
    fall_sentence,
    figures_of,
    file_meta,
    row_id,
)
from gtm_core.sequencer_snapshot_load import (
    PAYLOAD_MAX_AGE_S,
    load_payloads,
    profile_problem,
)
from gtm_core.sequencer_snapshot_status import status

__all__ = ["ack", "forget", "main", "stats_path", "status", "write"]


def stats_path(profile: str, content_root: Path | None = None) -> Path:
    from gtm_core.prospects_consolidate.paths import _pool_dir

    return _pool_dir(profile, content_root) / FILE_NAME


def _stamp_now(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: Path):
    """``(raw, problem)`` — ``problem`` is None for a missing or well-formed file."""
    if not path.exists():
        return None, None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None, "unreadable: the file is not valid JSON"
    rows = raw.get("sequences") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return None, "unreadable: it is neither a list of sequences nor an object holding one"
    meta = file_meta(raw)
    if meta["falls_invalid"]:
        return raw, (
            "edited: its recorded falls or re-dated rows are not in the shape the refresh "
            "command writes"
        )
    if meta["edited"]:
        return raw, "edited: the body no longer matches what the refresh command recorded"
    if any(not isinstance(r, dict) or not row_id(r) for r in rows):
        return raw, "unkeyable: a row has no sequence id, so a merge could not place it"
    ids = [row_id(r) for r in rows]
    if len(set(ids)) != len(ids):
        counts = Counter(ids)
        return raw, "duplicate-ids: " + ", ".join(
            printable(i) for i in sorted(i for i, n in counts.items() if n > 1)
        )
    return raw, None


def _newest(stamps: dict[str, str], fallback) -> str | None:
    from gtm_core.email_campaign_dashboard.health import _parse_fetched

    dated = [(w, s) for s in stamps.values() if (w := _parse_fetched(s)) is not None]
    return max(dated)[1] if dated else fallback


def _merge_falls(old: dict | None, changes: list[dict], stamp: str) -> dict:
    prev = {c["field"]: c for c in (old or {}).get("changes", [])}
    merged = []
    for c in changes:
        was = max(c["was"], prev.get(c["field"], {}).get("was", c["was"]))
        merged.append({"field": c["field"], "was": was, "now": c["now"]})
    kept = [c for f, c in prev.items() if f not in {m["field"] for m in merged}]
    return {"on": stamp, "changes": sorted(kept + merged, key=lambda c: c["field"])}


def _compose(raw, rows: list[dict], meta: dict) -> dict:
    ids = {row_id(r) for r in rows}
    stamps = {i: s for i, s in meta["stamps"].items() if i in ids}
    fetched = _newest(stamps, raw.get("fetched") if isinstance(raw, dict) else None)
    doc = {
        "format": FORMAT,
        "fetched": fetched,
        "sequences": rows,
        "stamps": stamps,
        "inherited": [i for i in meta["inherited"] if i in stamps],
        "falls": {i: f for i, f in meta["falls"].items() if i in stamps},
        "restamped": {i: s for i, s in meta["restamped"].items() if i in stamps},
        "payload_sha256": {i: h for i, h in meta["payload_sha256"].items() if i in stamps},
    }
    doc["body_sha256"] = body_digest(doc)
    return doc


def _previous(raw, problem, replace: bool) -> tuple[list[dict], dict, str | None]:
    blank = {"stamps": {}, "inherited": [], "falls": {}, "restamped": {}, "payload_sha256": {}}
    if replace:
        return [], blank, None
    if problem:
        return [], blank, problem
    if raw is None:
        return [], blank, None
    rows = [dict(r) for r in (raw.get("sequences") if isinstance(raw, dict) else raw)]
    return rows, file_meta(raw), None


def _replace_row(rows: list[dict], at: int, seq: dict, meta: dict, stamp: str) -> list[str]:
    """Swap in a sequence's new figures; the lines say what fell and what was re-dated."""
    sid, old = row_id(seq), rows[at]
    shown = printable(sid)
    before, after = figures_of(old), figures_of(seq)
    changes = [
        {"field": f, "was": before[f], "now": after[f]}
        for f in FALL_FIELDS
        if before[f] is not None and after[f] is not None and after[f] < before[f]
    ]
    notes = []
    if changes:
        meta["falls"][sid] = _merge_falls(meta["falls"].get(sid), changes, stamp)
        notes.append(f"{shown}: {fall_sentence(meta['falls'][sid])}")
    same = old == seq
    prior = meta["stamps"].get(sid)
    if prior:
        suffix = f", {IDENTICAL}" if same else ""
        notes.append(f"{shown}: re-dated {printable(prior)} -> {stamp}{suffix}")
    else:
        notes.append(f"{shown}: dated {stamp} (it had no date before)")
    if same and prior:
        meta["restamped"][sid] = prior
    else:
        meta["restamped"].pop(sid, None)
    rows[at] = seq
    return notes


def _apply(incoming: list[tuple[dict, str]], rows: list[dict], meta: dict, stamp: str) -> list[str]:
    """Merge the incoming rows into ``rows`` and ``meta`` in place; the lines for the operator."""
    notes: list[str] = []
    by_id = {row_id(r): n for n, r in enumerate(rows)}
    written: set[str] = set()
    for seq, sha in incoming:
        sid = row_id(seq)
        if sid in by_id:
            notes += _replace_row(rows, by_id[sid], seq, meta, stamp)
        else:
            by_id[sid] = len(rows)
            rows.append(seq)
        meta["stamps"][sid] = stamp
        meta["payload_sha256"][sid] = sha
        written.add(sid)
    meta["inherited"] = [i for i in meta["inherited"] if i not in written]
    return notes


def _page_refusal(why: str) -> str:
    return (
        f"REFUSED: the merged {FILE_NAME} would not read on the status page — {why}. "
        "Nothing was written. If the bad row is already in the file, `forget --id <its id>` "
        "removes it, or re-run with --replace to start the file again from this payload."
    )


def write(
    profile: str,
    payload_files: list[Path],
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    fetched: str | None = None,
    replace: bool = False,
    prune: bool = False,
    now: datetime | None = None,
) -> tuple[bool, list[str]]:
    """Merge the payload files' sequences into the snapshot. ``(ok, lines for the operator)``."""
    from gtm_core.email_campaign_dashboard.health import figures_age_days

    now = now or datetime.now(UTC)
    stamp = fetched or _stamp_now(now)
    if figures_age_days(stamp, now) is None:
        return False, [
            f"REFUSED: {printable(repr(stamp))} is not a usable date, or is in the future"
        ]
    if problem := profile_problem(profile, content_root, profiles_root):
        return False, [f"REFUSED: {problem}"]
    path = stats_path(profile, content_root)
    incoming, why = load_payloads(payload_files, now, path)
    if why:
        return False, [f"REFUSED: {why}"]
    with ledger_lock(path, create=True):
        raw, problem = _read(path)
        rows, meta, blocked = _previous(raw, problem, replace)
        if blocked:
            return False, [
                f"REFUSED: the existing {FILE_NAME} is {blocked}. "
                "Nothing was written. Re-run with --replace to start the file again from this payload."
            ]
        notes = _apply(incoming, rows, meta, stamp)
        if prune:
            keep = {row_id(s) for s, _ in incoming}
            dropped = [row_id(r) for r in rows if row_id(r) not in keep]
            rows = [r for r in rows if row_id(r) in keep]
            notes += [f"{printable(i)}: dropped (--prune)" for i in dropped]
        doc = _compose(raw, rows, meta)
        if bad := gate.page_problem(doc, now):
            return False, [_page_refusal(bad)]
        atomic_write_json(path, doc)
    return True, [f"Wrote {len(incoming)} sequence(s) to {FILE_NAME}, stamped {stamp}.", *notes]


def _edit(profile, content_root, change, profiles_root=None) -> tuple[bool, list[str]]:
    if problem := profile_problem(profile, content_root, profiles_root):
        return False, [f"REFUSED: {problem}"]
    path = stats_path(profile, content_root)
    with ledger_lock(path, create=True):
        raw, problem = _read(path)
        if raw is None or problem:
            return False, [
                f"REFUSED: the existing {FILE_NAME} is {problem or 'missing'}. Nothing was written."
            ]
        rows, meta, _ = _previous(raw, None, False)
        ok, lines = change(rows, meta)
        if ok:
            atomic_write_json(path, _compose(raw, rows, meta))
        return ok, lines


def forget(
    profile: str,
    sid: str,
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
) -> tuple[bool, list[str]]:
    shown = printable(sid)

    def change(rows, meta):
        if sid not in {row_id(r) for r in rows}:
            return False, [f"REFUSED: {shown} is not in {FILE_NAME}"]
        rows[:] = [r for r in rows if row_id(r) != sid]
        return True, [f"{shown}: removed from {FILE_NAME}"]

    return _edit(profile, content_root, change, profiles_root)


def ack(
    profile: str,
    sid: str | None = None,
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
):
    def change(rows, meta):
        gone = [sid] if sid else sorted(meta["falls"])
        if sid and sid not in meta["falls"]:
            return False, [f"REFUSED: {printable(sid)} has no recorded fall"]
        for i in gone:
            meta["falls"].pop(i, None)
        return True, [f"cleared {len(gone)} recorded fall(s)"]

    return _edit(profile, content_root, change, profiles_root)


_AGE_NOTE = f"""\
The payload age gate looks at the file's age: a payload file last saved more than
{PAYLOAD_MAX_AGE_S // 60} minutes ago, or modified ahead of this machine's clock, is refused. It is a
convenience guard against a stale export, not proof of when the figures were fetched. Rows go in as
{{"sequences": [<each reply's "payload">, ...]}}; a reply passed as it came is refused.
"""
_HASH_NOTE = """\
The hashes kept in the file are recorded by the refresh command. They show a later reader that
the file was changed by something other than this command; they do not show the figures are true.
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gtm_core.sequencer_snapshot",
        description=__doc__.split("\n")[0],
        epilog=_HASH_NOTE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--profile", required=True)
    ap.add_argument("--content-root", type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser(
        "write", epilog=_AGE_NOTE, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    w.add_argument("--payload", type=Path, action="append", required=True)
    w.add_argument("--fetched")
    w.add_argument("--replace", action="store_true")
    w.add_argument("--prune", action="store_true")
    f = sub.add_parser("forget")
    f.add_argument("--id", required=True)
    a = sub.add_parser("ack")
    a.add_argument("--id")
    sub.add_parser("status")
    args = ap.parse_args(argv)
    root = args.content_root or resolve_content_root()
    if args.cmd == "write":
        ok, lines = write(
            args.profile,
            args.payload,
            content_root=root,
            fetched=args.fetched,
            replace=args.replace,
            prune=args.prune,
        )
    elif args.cmd == "forget":
        ok, lines = forget(args.profile, args.id, content_root=root)
    elif args.cmd == "ack":
        ok, lines = ack(args.profile, args.id, content_root=root)
    else:
        ok, lines = status(args.profile, content_root=root)
    # Every line is escaped at its source; this is the belt for any that was not (§R5).
    print("\n".join(printable(line) for line in lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
