"""Write a research record onto an existing prospect list -- and refuse to write a bad one.

:mod:`gtm_core.signal_record` defines the eight columns that make a why-now checkable and
gates them at load. It does not put them on a row. Between those two facts sat 397 staged
recipients on four lists written before the columns existed, which
:mod:`gtm_core.account_integrity` reports as one file-level migration finding -- correctly,
and unhelpfully, since the finding names no route out.

This module is the route out. It merges a research pass (JSON, one record per recipient)
into a list CSV and emits a new dated list carrying the record.

**It is a gate on the researcher, not a convenience for one.** Every record is run through
:func:`gtm_core.signal_record.check_record` *before* it is written, against the clause it
will ship beside, and a record that blocks is REFUSED -- reported, not written, never
downgraded to a warning. That ordering is the whole point. The defect class this pipeline
keeps rediscovering is a plausible sentence about a real company that its own cited source
does not contain, and the one thing that reliably produces it is a research step allowed to
write first and be checked later. Refusing at write time means a list this module produced
cannot contain a record that would fail the load gate: the two run the same function.

**A row whose signal does not survive verification loses its clause.** Not its record --
its clause. If the source is stale, or does not say what the clause says, or was never
found, the honest row is one that makes no dated claim: the clause is cleared and the row
carries ``verdict: drop`` plus a reason. ``check_record`` already treats a clause-less row
as a generic arc needing only a verdict, so this is the contract's own shape rather than a
special case bolted on. Leaving an unsupported clause on the row and marking it ``drop``
would keep the false sentence one careless ``--require-verdict`` away from a real person.

Writes are all-or-nothing and round-tripped: the output is re-read and re-checked before it
replaces anything, and ``--out`` is always a new file, so the input list is never the thing
being edited.

Stdlib-only. Reads and writes only under the resolved content root, via the caller's paths.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .signal_record import RECORD_COLUMNS, SIGNAL_COLUMN, check_record

__all__ = [
    "RECORD_INPUT_FIELDS",
    "Refusal",
    "BackfillResult",
    "load_records",
    "apply_records",
    "write_list",
    "refuse_derived_target",
    "promote_records",
    "render",
    "main",
]

#: What a research record may carry. ``signal_clause`` is here because the clause is OURS to
#: rewrite: the contract is that the clause is a verbatim *reduction* of the evidence span, so
#: a pass that finds the real source and keeps a paraphrase written before the source was read
#: has not finished. ``email`` is the join key -- it is the one column that is unique per row on
#: every list in this tree, and unlike a name it cannot collide between two people at one company.
#: ``SIGNAL_COLUMN`` (added 2026-08-23) is here for the same reason ``hook_cell`` would be if
#: it were writable by this path: a research pass is exactly where the row's own signal is
#: first known, and ``load_records`` rejects anything not in this tuple by design -- so a
#: typo'd or renamed field is a load-time error, never a silently dropped one.
RECORD_INPUT_FIELDS = ("email", "signal_clause", SIGNAL_COLUMN, *RECORD_COLUMNS)


@dataclass(frozen=True)
class Refusal:
    """One record that will not be written, and the finding that stopped it."""

    email: str
    company: str
    rule: str
    detail: str


@dataclass
class BackfillResult:
    applied: int = 0
    cleared: int = 0
    unmatched: list[str] = field(default_factory=list)
    untouched: list[str] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    fieldnames: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.refusals or self.unmatched)


def load_records(path: Path) -> dict[str, dict]:
    """Read a research pass keyed by lowercased email.

    Rejects an unknown field rather than ignoring it: a typo'd ``signal_evidance`` that is
    silently dropped produces a row with no evidence and no complaint, which is the exact
    shape of failure this module exists to make impossible.
    """
    raw = json.loads(path.read_text())
    records = raw.get("records", raw) if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise ValueError(f"{path}: expected a list of records (or {{'records': [...]}})")
    out: dict[str, dict] = {}
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise ValueError(f"{path}: record {i} is not an object")
        unknown = sorted(set(rec) - set(RECORD_INPUT_FIELDS))
        if unknown:
            raise ValueError(f"{path}: record {i} has unknown field(s) {unknown}")
        email = (rec.get("email") or "").strip().lower()
        if not email:
            raise ValueError(f"{path}: record {i} has no email to join on")
        if email in out:
            raise ValueError(f"{path}: two records for {email!r}")
        out[email] = rec
    return out


def apply_records(
    rows: list[dict],
    fieldnames: list[str],
    records: dict[str, dict],
    *,
    as_of: datetime.date | None = None,
    matrix: object | None = None,
) -> BackfillResult:
    """Merge records into rows, refusing any that would not survive the load gate.

    Never mutates the input rows. A row with no record keeps exactly what it had -- this is
    resumable by construction, so a research pass can land one list, or one batch of a list,
    without the untouched remainder acquiring blank record columns that read as researched.

    ``matrix`` (a parsed ``gtm_core.hook_coverage.Matrix``) is optional and, when supplied,
    adds one more refusal: a ``signal_column`` value that is not a valid column for the row's
    OWN segment grid is refused exactly like a ``check_record`` block -- reported, not
    written, never silently accepted. Without a matrix the value is written unvalidated,
    matching every other optional gate in this module (``--case-study-file``,
    ``--hook-matrix`` on the linter side): a caller that wants the check must supply what it
    checks against.

    A list with no ``signal_clause`` column of its own (``ready-to-load.csv``, the production
    staged list -- its clause column is ``why_now``, per ``gtm_core.prospects_consolidate``;
    ``signal_clause`` is a name that belongs only to the derived ``ready-to-load-signal.csv``)
    gets the record's clause mirrored into ``why_now`` instead: cleared on a re-angle/drop with
    no clause, updated on a send with one supplied. Without this, ``why_now`` -- the sentence a
    human actually reads -- never changes even though the record verifies (or fails to).
    """
    res = BackfillResult()
    res.fieldnames = list(fieldnames) + [c for c in RECORD_COLUMNS if c not in fieldnames]
    if SIGNAL_COLUMN not in res.fieldnames and any(
        SIGNAL_COLUMN in rec for rec in records.values()
    ):
        res.fieldnames.append(SIGNAL_COLUMN)
    mirror_to_why_now = "signal_clause" not in fieldnames and "why_now" in fieldnames
    seen: set[str] = set()

    for row in rows:
        out = dict(row)
        for col in RECORD_COLUMNS:
            out.setdefault(col, "")
        email = (row.get("email") or "").strip().lower()
        rec = records.get(email)
        if rec is None:
            res.untouched.append(email)
            res.rows.append(out)
            continue
        seen.add(email)

        candidate = dict(out)
        for k, v in rec.items():
            if k == "email":
                continue
            candidate[k] = (v or "").strip()

        if mirror_to_why_now:
            candidate["why_now"] = candidate.get("signal_clause") or ""

        findings = [f for f in check_record(candidate, as_of=as_of) if f.level == "block"]
        signal_value = (candidate.get(SIGNAL_COLUMN) or "").strip()
        if signal_value and matrix is not None and getattr(matrix, "ok", False):
            from .hook_coverage import signal_columns_for_segment

            valid = signal_columns_for_segment(matrix, candidate.get("segment") or "")
            if not any(signal_value.lower() == s.lower() for s in valid):
                res.refusals.append(
                    Refusal(
                        email,
                        (row.get("company") or "").strip(),
                        "signal-column-unknown",
                        f"{SIGNAL_COLUMN} {signal_value!r} is not a valid signal for this "
                        f"row's segment grid ({len(valid)} valid: {', '.join(sorted(valid)) or 'none'})",
                    )
                )
                res.rows.append(out)
                continue
        if findings:
            for f in findings:
                res.refusals.append(
                    Refusal(email, (row.get("company") or "").strip(), f.rule, f.detail)
                )
            # The row keeps its pre-record state. A refused record is not a partial write.
            res.rows.append(out)
            continue

        if not (candidate.get("signal_clause") or "").strip():
            res.cleared += 1
        res.applied += 1
        res.rows.append(candidate)

    res.unmatched = sorted(set(records) - seen)
    return res


def write_list(res: BackfillResult, out_path: Path) -> None:
    """Write the merged list, then re-read it and re-check every record it claims to carry.

    The round-trip is not ceremony. CSV quoting turns a newline inside an evidence span into
    a new row, and a verbatim source span is exactly the field most likely to contain one --
    so the write is the step that can invent a defect the in-memory check already passed.
    """
    tmp = out_path.with_suffix(out_path.suffix + ".partial")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=res.fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(res.rows)

    with tmp.open(newline="", encoding="utf-8") as fh:
        back = list(csv.DictReader(fh))
    if len(back) != len(res.rows):
        tmp.unlink(missing_ok=True)
        raise ValueError(f"{out_path}: round-trip lost rows ({len(back)} != {len(res.rows)})")
    check_cols = (
        (*RECORD_COLUMNS, SIGNAL_COLUMN) if SIGNAL_COLUMN in res.fieldnames else RECORD_COLUMNS
    )
    for before, after in zip(res.rows, back, strict=True):
        for col in check_cols:
            if (before.get(col) or "") != (after.get(col) or ""):
                tmp.unlink(missing_ok=True)
                raise ValueError(
                    f"{out_path}: {col!r} did not survive the write for {before.get('email')!r}"
                )
    tmp.replace(out_path)


def render(res: BackfillResult, *, list_path: Path, out_path: Path) -> str:
    lines = [
        f"signal backfill -- {list_path.name} -> {out_path.name}",
        f"  rows            : {len(res.rows)}",
        f"  records applied : {res.applied}"
        + (f" ({res.cleared} clause cleared -- signal did not survive)" if res.cleared else ""),
        f"  rows untouched  : {len(res.untouched)}",
    ]
    if res.unmatched:
        lines.append(f"  records matching no row: {len(res.unmatched)}")
        for e in res.unmatched[:5]:
            lines.append(f"      {e}")
    if res.refusals:
        lines.append(f"  REFUSED         : {len(res.refusals)} record(s) would not pass the gate")
        for r in res.refusals[:20]:
            lines.append(f"      {r.rule}: {r.company or r.email} -- {r.detail}")
        if len(res.refusals) > 20:
            lines.append(f"      ... {len(res.refusals) - 20} more")
    lines.append("FAIL -- nothing written." if res.failed else "PASS")
    return "\n".join(lines)


def promote_records(
    records: dict[str, dict],
    rows: list[dict],
    profile: str,
    *,
    content_root: Path | None = None,
    today: str | None = None,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """Build ``latest.json`` items carrying each record, plus the records that have no account.

    This is the durable half of a backfill, and the reason it lives here rather than in a
    caller's script: a record written to a pool CSV is discarded by the next
    ``consolidate``, so the ONLY place a research record survives is the account. Leaving
    that as a documented three-step recipe is what produced two bespoke promotion scripts
    and one lost batch on 2026-08-29 -- the recipe was correct and nobody ran it.

    Each item is a FULL COPY of the account's existing record with only the record's own
    fields replaced. ``upsert_latest`` merges field by field — a blank incoming value never
    overwrites a populated one — so a partial item would be safe too; the full copy is kept
    because it makes the promoted item readable on its own.

    A record whose row has no ``account_id`` cannot be promoted and is RETURNED, never
    dropped: the caller has to see that it has nowhere durable to go.

    A record that carries a ``verdict`` stamps ``verdict_on`` with ``today``: research set
    that verdict now, from a record ``check_record`` passed. ``prospects_consolidate`` lifts
    a pool row's ``re-angle`` to the account's ``send`` only on a stamp newer than the row's.
    """
    stamp = today or datetime.date.today().isoformat()
    from .prospects_state import load_latest

    by_email = {(r.get("email") or "").strip().lower(): r for r in rows}
    items_by_id = {
        str(i.get("account_id") or "").strip(): i
        for i in load_latest(profile, content_root).get("items", [])
        if str(i.get("account_id") or "").strip()
    }

    items: list[dict] = []
    orphans: list[tuple[str, str]] = []
    for email, rec in records.items():
        row = by_email.get(email)
        if row is None:
            orphans.append((email, "no matching row in the list"))
            continue
        base = items_by_id.get(str(row.get("account_id") or "").strip())
        if base is None:
            orphans.append(
                (email, f"row for {row.get('company', '?')!r} has no account in latest.json")
            )
            continue
        item = dict(base)
        for col in (*RECORD_COLUMNS, SIGNAL_COLUMN):
            value = str(rec.get(col) or "").strip()
            if value:
                item[col] = value
        # The clause travels with its provenance -- `prospects_consolidate` treats
        # `why_now` + source/observed/evidence as one atomic record when carrying it down.
        clause = str(rec.get("signal_clause") or "").strip()
        if clause:
            item["why_now"] = clause
        if str(rec.get("verdict") or "").strip():
            item["verdict_on"] = stamp
        items.append(item)
    return items, orphans


#: Files `prospects_consolidate` REGENERATES. Writing a record into one of these looks like
#: it worked and is undone by the next sweep — by another session, or by a scheduled run.
#:
#: This is not hypothetical. On 2026-08-29 a backfill pass wrote 34 verified records into
#: `ready-to-load.csv`, watched them vanish, diagnosed it as a concurrency race, re-applied
#: them under `gtm_core.locks.profile_lock`, and lost them again — because a lock serialises
#: writers and does nothing about a file that is rebuilt from other sources. The durable home
#: for a research record is the ACCOUNT, in `latest.json`, from which `consolidate` re-derives
#: it onto every row on every sweep.
#:
#: The default `--out` (``<list>-recorded.csv``) was never affected. This only refuses an
#: explicit `--out` aimed at a build output.
_DERIVED_ARTIFACTS = frozenset(
    {
        "ready-to-load.csv",
        "ready-to-load-signal.csv",
        "ready-to-load-generic.csv",
        "master-list.csv",
        "needs-verification.csv",
    }
)


def refuse_derived_target(out_path: Path) -> str:
    """The reason ``out_path`` must not be written, or ``""`` when it is safe."""
    if out_path.name not in _DERIVED_ARTIFACTS:
        return ""
    return (
        f"refusing to write {out_path.name!r}: prospects_consolidate REGENERATES it, so a "
        f"record written here is silently discarded by the next sweep (and a lock does not "
        f"help -- the file is rebuilt, not raced).\n"
        f"  Re-run with --promote --profile <p> instead: it writes each record onto its\n"
        f"  ACCOUNT in latest.json, which survives every rebuild, and consolidate then\n"
        f"  carries it back onto every row of this list.\n"
        f"  To produce a side-by-side copy for review, drop --out and take the default "
        f"<list>-recorded.csv."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m gtm_core.signal_backfill",
        description="Merge a verified research record into a prospect list CSV.",
    )
    ap.add_argument("--list", dest="list_path", required=True, type=Path)
    ap.add_argument("--records", dest="records_path", required=True, type=Path)
    ap.add_argument("--out", dest="out_path", type=Path, help="default: <list>-recorded.csv")
    ap.add_argument("--as-of", help="ISO date for the freshness check (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="check and report; write nothing")
    ap.add_argument(
        "--promote",
        action="store_true",
        help="ALSO write each record onto its ACCOUNT in latest.json, which is the only "
        "place it survives a rebuild. Requires --profile. Use this instead of aiming --out "
        "at a pool CSV: consolidate regenerates those, so a record written there is gone on "
        "the next sweep.",
    )
    ap.add_argument("--profile", help="active profile; required by --promote")
    ap.add_argument(
        "--hook-matrix",
        type=Path,
        help="profile's hook-matrix.md -- validates any signal_column value against the "
        "row's own segment grid, same opt-in shape as merge_render_linter's --hook-matrix. "
        "Without it, signal_column is written unvalidated.",
    )
    args = ap.parse_args(argv)

    as_of = datetime.date.fromisoformat(args.as_of) if args.as_of else None
    out_path = args.out_path or args.list_path.with_name(
        args.list_path.stem + "-recorded" + args.list_path.suffix
    )

    matrix = None
    if args.hook_matrix:
        from .hook_coverage import parse_matrix

        matrix = parse_matrix(args.hook_matrix)

    with args.list_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    refusal = refuse_derived_target(out_path)
    if refusal and not args.dry_run:
        print(refusal, file=sys.stderr)
        return 2

    res = apply_records(
        rows, fieldnames, load_records(args.records_path), as_of=as_of, matrix=matrix
    )
    print(render(res, list_path=args.list_path, out_path=out_path))
    if res.failed:
        return 1
    if args.promote and not args.profile:
        print("--promote requires --profile", file=sys.stderr)
        return 2
    if args.dry_run:
        return 0

    write_list(res, out_path)

    if args.promote:
        from .prospects_state import upsert_latest

        items, orphans = promote_records(load_records(args.records_path), res.rows, args.profile)
        for email, why in orphans:
            print(f"NOT PROMOTED {email}: {why}", file=sys.stderr)
        if items:
            out = upsert_latest(
                args.profile, items, source_run=f"backfill-{args.records_path.stem}"
            )
            print(
                f"promoted {len(items)} record(s) onto accounts in latest.json "
                f"(updated {out.get('updated', '?')}, total {out.get('total', '?')}); "
                f"re-run consolidate to carry them onto every row"
            )
        if orphans:
            # An unpromotable record is not a partial success: it has nowhere durable to
            # live, and reporting 0 while printing warnings is how that gets missed.
            return 3
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
