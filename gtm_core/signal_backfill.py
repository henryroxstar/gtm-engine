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
    """
    res = BackfillResult()
    res.fieldnames = list(fieldnames) + [c for c in RECORD_COLUMNS if c not in fieldnames]
    if SIGNAL_COLUMN not in res.fieldnames and any(
        SIGNAL_COLUMN in rec for rec in records.values()
    ):
        res.fieldnames.append(SIGNAL_COLUMN)
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

    res = apply_records(
        rows, fieldnames, load_records(args.records_path), as_of=as_of, matrix=matrix
    )
    print(render(res, list_path=args.list_path, out_path=out_path))
    if res.failed:
        return 1
    if not args.dry_run:
        write_list(res, out_path)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
