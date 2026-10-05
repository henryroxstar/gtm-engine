"""Does the sequencer's live state match what our records say happened to it?

A post-mortem (2026-10-02) found 201 prospects loaded into live sequences by an agent surface that
never wrote to ``history.jsonl``, placeholder sequence ids sitting in ``cells.toml`` for a week, and
three sequences switched on with no activation on record. Each was visible in the sequencer at any
moment; nothing compared it with our own ledger. This is that comparison.

It is **MCP-free**, like :mod:`gtm_core.email_compliance`: the caller fetches ``list_sequences`` and
one ``get_sequence_stats`` reply per sequence over MCP and pipes them in as JSON files. It reads the
history ledger (:mod:`gtm_core.sequencer_history` says how) and ``sequences/cells.toml``, and
reports one plain-words line per finding, with a severity:

========================================  ========  ==========================================
code                                      severity  what it says
========================================  ========  ==========================================
``active-without-activation-event``       ERROR     switched on, but history's last word on it
                                                    is not an activation
``prospects-without-load-event``          ERROR     it holds more prospects than recorded
                                                    loads account for
``sequence-unregistered``                 ERROR     live, but not in ``cells.toml``, so replies
                                                    cannot be attributed to a cell
``placeholder-id-registered``             WARN      ``cells.toml`` names an id that is not a
                                                    sequencer id and matches no live sequence
``inactive-without-pause-event``          WARN      activated, now off, no pause on record
``stats-missing`` ``stats-without-listing`` ``events-exceed-live-total``   WARN
                                                    the inputs or the ledger disagree in a way
                                                    the five checks above could not see
========================================  ========  ==========================================

    python -m gtm_core.sequencer_reconcile --profile P --sequences list.json --stats-dir DIR \\
        [--json] [--record]

Exit **0** clean (warnings do not fail), **2** any ERROR, **3** an input is unreadable or lacks a
field the join needs. A usage mistake is also 3, never 2: argparse's own exit 2 would read as a
finding.

**``--record``** appends the three retroactive events (``sequence_loaded_unrecorded``,
``sequence_activated_by_operator``, ``sequence_paused_by_operator``), each labelled
``recorded_retroactively: true`` with the evidence, through :class:`gtm_core.ledgers.Ledgers` — the
path ``ledger_cli append-history`` uses. It never runs without the flag and never edits
``cells.toml``. It is idempotent: each event is re-validated against the ledger as it stands
immediately before it is written, so a finding already recorded writes nothing. A second process
appending between that check and the write is not excluded (the ledger's own lock covers the
append, not the check); this is an operator command, run one at a time.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from gtm_core.cells import cells_map_path, load_cell_map
from gtm_core.ledgers import Ledgers
from gtm_core.paths import _safe_segment, resolve_content_root
from gtm_core.sequencer_history import (
    ACTIVATED,
    PAUSED,
    SeqHistory,
    index_history,
    read_history,
)
from gtm_core.sequencer_stats_read import (
    PayloadError,
    StatsFigures,
    is_provider_id,
    list_rows,
    load_stats_dir,
    read_json,
    shown,
)

SKILL = "sequencer-reconcile"
#: The payload shapes this reads are the Saleshandy API's (``Sequence Id``, ten-character ids).
PROVIDER = "saleshandy"
_SEVERITY_ORDER = {"ERROR": 0, "WARN": 1}
#: finding code -> the retroactive event that records it
_RECORDABLE = {
    "prospects-without-load-event": "sequence_loaded_unrecorded",
    "active-without-activation-event": "sequence_activated_by_operator",
    "inactive-without-pause-event": "sequence_paused_by_operator",
}


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    sequence_id: str
    title: str
    message: str
    evidence: dict

    def line(self) -> str:
        return f"{self.severity} {self.code}: {self.message}"

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _who(title: str, sid: str) -> str:
    return f'"{shown(title, 80)}" ({shown(sid)})' if title else f"sequence {shown(sid)}"


# ── cells.toml ──────────────────────────────────────────────────────────────────────────────


def registered_ids(profile: str, content_root: Path) -> tuple[set[str], set[str]]:
    """``(every id cells.toml names, the ids the reply-attribution join can use)``.

    The second is :func:`gtm_core.cells.load_cell_map`'s own answer (a row needs ``id``, ``csv`` and
    ``spec``); the first is read raw so a half-written row, which the join drops, is still seen. A
    file that is present but not TOML stops the run: ``load_cell_map`` would return no rows and
    every live sequence would read as unregistered for the wrong reason.
    """
    path = cells_map_path(profile, content_root)
    if not path.is_file():
        return set(), set()
    try:
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PayloadError(f"cells.toml is not readable TOML ({type(exc).__name__})") from exc
    raw = {r["id"] for r in doc.get("sequence", []) if isinstance(r, dict) and r.get("id")}
    return raw, {r["sequence_id"] for r in load_cell_map(profile, content_root)}


# ── the checks ──────────────────────────────────────────────────────────────────────────────


def _registration(sid, title, joinable, raw) -> list[Finding]:
    if sid in joinable:
        return []
    why = "its cells.toml entry lacks a csv or spec" if sid in raw else "it is not in cells.toml"
    msg = f"{_who(title, sid)} is live in the sequencer but {why}, so its replies cannot be "
    msg += "attributed to a cell."
    return [
        Finding("ERROR", "sequence-unregistered", sid, title, msg, {"in_cells_toml": sid in raw})
    ]


def _state(sid, title, active: bool, hist: SeqHistory) -> list[Finding]:
    last = hist.last_state
    ev = {"history_last_state": last}
    if active and last != ACTIVATED:
        tail = (
            "history's last word on it is that it was paused"
            if last == PAUSED
            else "history has no record of anyone activating it"
        )
        msg = f"{_who(title, sid)} is switched on in the sequencer, but {tail}."
        return [Finding("ERROR", "active-without-activation-event", sid, title, msg, ev)]
    if not active and last == ACTIVATED:
        msg = f"{_who(title, sid)} was activated and is now off, but history has no record of "
        msg += "it being paused."
        return [Finding("WARN", "inactive-without-pause-event", sid, title, msg, ev)]
    return []


def _prospects(sid, title, fig: StatsFigures | None, hist: SeqHistory) -> list[Finding]:
    who = _who(title, sid)
    if fig is None:
        msg = f"{who} is live but no get_sequence_stats file was given for it, so its prospect "
        msg += "count was not checked."
        return [Finding("WARN", "stats-missing", sid, title, msg, {})]
    ev = {"live_total": fig.total, "accounted": hist.accounted}
    if fig.total > hist.accounted:
        gap = fig.total - hist.accounted
        msg = f"{who} holds {fig.total} prospects; recorded loads account for {hist.accounted}, "
        msg += f"so {gap} were loaded with no record."
        return [
            Finding("ERROR", "prospects-without-load-event", sid, title, msg, {**ev, "gap": gap})
        ]
    if fig.total < hist.accounted:
        msg = f"{who} holds {fig.total} prospects but history records {hist.accounted} loaded: "
        msg += "a double count, or prospects removed since."
        return [Finding("WARN", "events-exceed-live-total", sid, title, msg, ev)]
    return []


def check_sequence(row, fig, hist, joinable, raw) -> list[Finding]:
    """Every finding about one live sequence (a row of ``list_sequences``)."""
    sid = row["id"]
    title = (row.get("title") if isinstance(row.get("title"), str) else "") or (
        fig.name if fig else ""
    )
    return (
        _registration(sid, title, joinable, raw)
        + _state(sid, title, row["active"], hist)
        + _prospects(sid, title, fig, hist)
    )


def _placeholders(raw: set[str], live: set[str]) -> list[Finding]:
    out = []
    for rid in sorted(raw):
        if is_provider_id(rid) or rid in live:
            continue
        draft = rid.upper().startswith("DRAFT-")
        tail = (
            "a draft id that stays until the sequence is staged"
            if draft
            else "a placeholder that was never replaced by the real sequence id"
        )
        msg = f'cells.toml registers "{shown(rid, 100)}", which is not a sequencer id and matches '
        msg += f"no live sequence: {tail}."
        out.append(Finding("WARN", "placeholder-id-registered", rid, "", msg, {"draft": draft}))
    return out


def reconcile(live, stats, history_rows, raw_ids, joinable) -> list[Finding]:
    """Pure: the live rows, the stats, the ledger and ``cells.toml`` in; the findings out."""
    index = index_history(history_rows)
    live_ids = {r["id"] for r in live}
    findings: list[Finding] = []
    for row in live:
        hist = index.get(row["id"], SeqHistory())
        findings += check_sequence(row, stats.get(row["id"]), hist, joinable, raw_ids)
    for sid in sorted(set(stats) - live_ids):
        msg = f"a stats file is for sequence {shown(sid)}, which the sequence list does not show: "
        msg += "a stale file, or a missing page of the list."
        findings.append(Finding("WARN", "stats-without-listing", sid, stats[sid].name, msg, {}))
    findings += _placeholders(raw_ids, live_ids)
    # Within a severity the draft ids go last: the expected "not staged yet" lines must not bury the
    # placeholders that were never replaced.
    return sorted(
        findings,
        key=lambda f: (
            _SEVERITY_ORDER[f.severity],
            bool(f.evidence.get("draft")),
            f.sequence_id,
            f.code,
        ),
    )


# ── --record ────────────────────────────────────────────────────────────────────────────────

_NOTES = {
    "sequence_loaded_unrecorded": (
        "The sequencer held {live_total} prospects against {accounted} recorded; this row "
        "accounts for the difference. When and by what route they were loaded is not known "
        "from this record."
    ),
    "sequence_activated_by_operator": (
        "The sequencer showed the sequence switched on with no activation on record. Who did "
        "it, and when, is not known from this record."
    ),
    "sequence_paused_by_operator": (
        "The sequencer showed the sequence off after a recorded activation, with no pause on "
        "record. When it was paused is not known from this record."
    ),
}


def _retro_event(finding: Finding, evidence_source: str, today: str) -> dict:
    name = _RECORDABLE[finding.code]
    event = {
        "event": name,
        "skill": SKILL,
        "provider": PROVIDER,
        "sequence_id": finding.sequence_id,
        "sequence": finding.title,
        "occurred_at": None,
        "recorded_retroactively": True,
        "recorded_on": today,
        "evidence": evidence_source,
        "note": _NOTES[name].format(**finding.evidence),
    }
    if name == "sequence_loaded_unrecorded":
        event["prospects_loaded"] = finding.evidence["gap"]
    else:
        event["active"] = name == "sequence_activated_by_operator"
    return event


def record(
    findings: list[Finding],
    *,
    profile: str,
    content_root: Path,
    live: list[dict],
    stats: dict[str, StatsFigures],
    raw_ids: set[str],
    joinable: set[str],
    evidence_source: str,
    now: datetime | None = None,
) -> tuple[list[dict], list[str]]:
    """Append the retroactive events for the recordable findings. ``(written, skipped notes)``.

    Each finding is re-validated against the ledger as it stands just before its event is written:
    one that no longer holds (recorded by this command or another) writes nothing.
    """
    ledgers = Ledgers(SimpleNamespace(content_root=content_root), profile)
    history_path = content_root / profile / "history.jsonl"
    today = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
    by_id = {r["id"]: r for r in live}
    written: list[dict] = []
    skipped: list[str] = []
    for finding in findings:
        if finding.code not in _RECORDABLE:
            continue
        sid = finding.sequence_id
        hist = index_history(read_history(history_path)).get(sid, SeqHistory())
        fresh = check_sequence(by_id[sid], stats.get(sid), hist, joinable, raw_ids)
        current = next((f for f in fresh if f.code == finding.code), None)
        if current is None:
            skipped.append(f"{finding.code} for {shown(sid)} is already recorded")
            continue
        event = _retro_event(current, evidence_source, today)
        ledgers.append_history(event)
        written.append(event)
    return written, skipped


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # a usage mistake is exit 3: argparse's own 2 would read as a finding
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(3)


def _parser() -> argparse.ArgumentParser:
    ap = _Parser(
        prog="gtm_core.sequencer_reconcile",
        description="Compare the sequencer's live state with history.jsonl and cells.toml.",
    )
    ap.add_argument("--profile", required=True)
    ap.add_argument("--sequences", required=True, type=Path, help="list_sequences reply (JSON)")
    ap.add_argument(
        "--stats-dir",
        required=True,
        type=Path,
        help="directory of get_sequence_stats replies, one *.json per sequence",
    )
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    ap.add_argument(
        "--record",
        action="store_true",
        help="append labelled retroactive events to history.jsonl for what it found "
        "(without this flag nothing is written)",
    )
    return ap


def _render(result: dict) -> str:
    lines = [
        f"sequencer_reconcile - profile {shown(result['profile'])}: {result['checked']} live "
        f"sequence(s) checked, {result['errors']} error(s), {result['warnings']} warning(s)"
    ]
    lines += [f["line"] for f in result["findings"]]
    if not result["findings"]:
        lines.append(
            "OK: every live sequence is registered, its prospects are accounted for, and its "
            "on/off state matches the history ledger."
        )
    if "recorded" in result:
        lines.append(f"recorded {len(result['recorded'])} retroactive event(s) in history.jsonl")
        lines += [f"  skipped: {s}" for s in result["skipped"]]
        if result["recorded"]:
            lines.append("run it again to confirm the ledger is now clean")
    return "\n".join(lines)


def run(args: argparse.Namespace, *, now: datetime | None = None) -> tuple[int, dict]:
    try:
        profile = _safe_segment(args.profile, "profile")
    except ValueError as exc:
        raise PayloadError(str(exc)) from exc
    root = resolve_content_root()
    if not (root / profile).is_dir():
        raise PayloadError(
            f"no content folder for profile {shown(profile)} under {shown(str(root))}"
        )
    live = list_rows(read_json(args.sequences, "--sequences"), "--sequences")
    stats = load_stats_dir(args.stats_dir, need_emails=False)
    raw_ids, joinable = registered_ids(profile, root)
    findings = reconcile(
        live, stats, read_history(root / profile / "history.jsonl"), raw_ids, joinable
    )
    result = {
        "profile": profile,
        "checked": len(live),
        "errors": sum(f.severity == "ERROR" for f in findings),
        "warnings": sum(f.severity == "WARN" for f in findings),
        "findings": [{**f.as_dict(), "line": f.line()} for f in findings],
    }
    if args.record:
        source = (
            f"live list_sequences ({shown(args.sequences.name)}) and {len(stats)} "
            "get_sequence_stats reply(ies) read by gtm_core.sequencer_reconcile"
        )
        written, skipped = record(
            findings,
            profile=profile,
            content_root=root,
            live=live,
            stats=stats,
            raw_ids=raw_ids,
            joinable=joinable,
            evidence_source=source,
            now=now,
        )
        result["recorded"] = [
            {"event": e["event"], "sequence_id": e["sequence_id"]} for e in written
        ]
        result["skipped"] = skipped
    return (2 if result["errors"] else 0), result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        code, result = run(args)
    except PayloadError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else _render(result))
    return code


if __name__ == "__main__":
    sys.exit(main())
