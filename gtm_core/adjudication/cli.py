from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from ..signal_record import JUDGE_COLUMNS
from .completeness import completeness, covered_classes
from .defects import normalize_defect_class
from .disposal import disposal_audit
from .io import _read_fieldnames, _read_rows, read_records, write_records
from .model import DEFAULT_STRATA, Adjudication
from .repair import REPAIR_ATTEMPT_CAP, repair_queue
from .report import (
    REGENERATION_CAP,
    defect_report,
    regeneration_count,
    require_qa,
    source_name,
)
from .sampling import collapsed_axes, coverage, sample, stratify, stratum_of
from .stability import unstable_bodies, unstable_row_id
from .tally import send_set, tally, write_tally
from .verdicts import novel_classes, rank, worst_verdict


def _known_from(args) -> list[str]:
    known = list(args.known or [])
    if args.known_file:
        known += [
            ln.strip() for ln in Path(args.known_file).read_text(encoding="utf-8").splitlines()
        ]
    return known


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="gtm_core.adjudication",
        description=(
            "The reading pass as a ranker: sample a list so the read covers it, record "
            "a verdict per email, rank the send order, and separate defect classes that "
            "need a NEW rule from ones an existing rule should already have caught. "
            "Never a gate — nothing here blocks a send."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sample", help="pick a covering sample to read")
    sp.add_argument("--csv", required=True, type=Path)
    sp.add_argument("-n", type=int, default=30)
    sp.add_argument("--axes", default=",".join(DEFAULT_STRATA))
    sp.add_argument("--out", type=Path, help="write the sample as CSV (default: stdout summary)")

    cp = sub.add_parser("coverage", help="what the read covered, and what it did not")
    cp.add_argument("--csv", required=True, type=Path)
    cp.add_argument("--records", required=True, type=Path)
    cp.add_argument("--axes", default=",".join(DEFAULT_STRATA))

    rp = sub.add_parser("rank", help="send order from the recorded verdicts")
    rp.add_argument("--records", required=True, type=Path)
    rp.add_argument("--top", type=int, default=0, help="print only the first N")

    np_ = sub.add_parser("novel", help="classes that need a new rule vs. an inert one")
    np_.add_argument("--records", required=True, type=Path)
    np_.add_argument("--known", action="append", default=[], metavar="RULE")
    np_.add_argument("--known-file", type=Path, help="one rule name per line")

    drp = sub.add_parser(
        "defect-report", help="per-source defect classes + operator guidance → markdown"
    )
    drp.add_argument("--records", required=True, type=Path, nargs="+")
    drp.add_argument("--known-file", type=Path, help="one linter rule name per line (--list-rules)")
    drp.add_argument("--feedback", type=Path, help="operator-feedback.jsonl from the hold queue")
    drp.add_argument("--out", required=True, type=Path, help="must be under the content root (PII)")
    drp.add_argument("--content-root", type=Path, default=None)

    rgp = sub.add_parser(
        "regen-count", help="how many times a spec was regenerated; refuses at the cap"
    )
    rgp.add_argument("--spec", required=True, type=Path)
    rgp.add_argument("--cap", type=int, default=REGENERATION_CAP)

    rqp = sub.add_parser(
        "require-qa",
        help="refuse staging without a merge-render QA record for the spec's CURRENT bytes",
    )
    rqp.add_argument("--spec", required=True, type=Path)
    rqp.add_argument("--qa-dir", required=True, type=Path)
    # The unit of a merge-render run is (spec x csv). Without this the gate answers a
    # weaker question than the caller is asking — see require_qa's docstring.
    rqp.add_argument("--csv", type=Path, help="the list to be staged; matched by hash too")

    qp = sub.add_parser(
        "repair-queue",
        help="rows needing another repair pass — and the ones that must NOT get one",
    )
    qp.add_argument("--records", required=True, type=Path)
    qp.add_argument("--cap", type=int, default=REPAIR_ATTEMPT_CAP)
    qp.add_argument("--out", type=Path, help="write the eligible rows as JSONL")

    tp = sub.add_parser(
        "tally",
        help="tally independent judging runs of the same pool by (email, touch) — "
        "reports a send-count RANGE, never a single number",
    )
    tp.add_argument("--records", required=True, type=Path, nargs="+", help="one file per run")
    tp.add_argument("--out", required=True, type=Path, help="write per-unit tally rows as JSONL")
    tp.add_argument(
        "--max-contested",
        type=float,
        default=0.10,
        help="exit 1 (file is still written) when this fraction of units have no majority",
    )
    tp.add_argument(
        "--group-by",
        choices=("email", "row_id"),
        default="email",
        help="grouping key; row_id is refused unless every record's row_id is already "
        "stable per (email, touch) — see unstable_row_id()",
    )

    dp = sub.add_parser(
        "disposal",
        help="where the NON-send rows went — fails on stranded re-angles and misfiled drops",
    )
    dp.add_argument("--records", required=True, type=Path, nargs="+")
    dp.add_argument(
        "--accounted-for",
        type=Path,
        help="file of emails (one per line) that were queued, enrolled, or explicitly retired",
    )
    dp.add_argument("--cap", type=int, default=REPAIR_ATTEMPT_CAP)

    cop = sub.add_parser(
        "check-complete",
        help="fail unless there is a record for EVERY row — the partial-batch guard",
    )
    cop.add_argument("--csv", required=True, type=Path)
    cop.add_argument("--records", required=True, type=Path)

    wv = sub.add_parser(
        "write-verdicts",
        help="write recorded verdicts into a CSV's judge_verdict columns (never `verdict`, "
        "which is the researcher's)",
    )
    wv.add_argument("--csv", required=True, type=Path)
    wv.add_argument("--records", required=True, type=Path)
    wv.add_argument("--out", required=True, type=Path)
    wv.add_argument(
        "--profile",
        default="",
        help="stamp judge_calibrated from this profile's NEWEST sealed holdout: true only when "
        "`eval_calibration score` recorded a PASS against it; omitted leaves it blank, which "
        "reads as 'never checked' rather than 'checked and failed'",
    )

    args = p.parse_args(argv)
    axes = (
        tuple(a.strip() for a in getattr(args, "axes", "").split(",") if a.strip())
        or DEFAULT_STRATA
    )

    if args.cmd == "sample":
        rows = _read_rows(args.csv)
        picked = sample(rows, args.n, axes)
        buckets = stratify(rows, axes)
        print(f"{len(rows)} row(s) in {len(buckets)} stratum/strata; sampled {len(picked)}")
        flat = collapsed_axes(rows, axes)
        if flat:
            print(
                "  note: "
                + ", ".join(flat)
                + " does not vary on this list (absent or constant) — the sample covers "
                "fewer dimensions than the axes suggest"
            )
        for key in sorted(buckets):
            got = sum(1 for r in picked if stratum_of(r, axes) == key)
            print(f"  {key:40} {got:3}/{len(buckets[key])}")
        if args.out and picked:
            with args.out.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(picked)
            print(f"\nwrote {args.out}")
        return 0

    if args.cmd == "coverage":
        c = coverage(_read_rows(args.csv), read_records(args.records), axes)
        print(
            f"coverage — {c.adjudicated} email(s) read across {c.read}/{c.strata} strata "
            f"({c.share:.0%}), {c.rows} row(s) in the list"
        )
        for key in c.unread_strata:
            print(f"  UNREAD  {key}")
        if not c.unread_strata:
            print("  every stratum read")
        return 0

    if args.cmd == "rank":
        ordered = rank(read_records(args.records))
        shown = ordered[: args.top] if args.top else ordered
        by_verdict = Counter(a.verdict for a in ordered)
        print(
            f"{len(ordered)} adjudicated — " + ", ".join(f"{k}={v}" for k, v in by_verdict.items())
        )
        for i, a in enumerate(shown, 1):
            cls = f"  [{a.defect_class}]" if a.defect_class else ""
            print(f"  {i:3}. {a.score} {a.verdict:9} {a.email}{cls}")
        return 0

    if args.cmd == "tally":
        sources: dict[str, list[Adjudication]] = {
            path.stem: read_records(path) for path in args.records
        }
        all_records = [rec for recs in sources.values() for rec in recs]

        if args.group_by == "row_id":
            msg = unstable_row_id(all_records)
            if msg:
                print(f"refusing --group-by row_id: {msg}", file=sys.stderr)
                return 2

        bad_bodies = unstable_bodies(all_records)
        if bad_bodies:
            print(
                f"refusing: {len(bad_bodies)} (email, touch) unit(s) carry more than one "
                f"body_hash across the given files — this would compare verdicts on "
                f"DIFFERENT emails, not judge variance:",
                file=sys.stderr,
            )
            for email, touch in bad_bodies[:10]:
                print(f"  {email} touch {touch}", file=sys.stderr)
            return 2

        t = tally(sources, max_contested=args.max_contested, group_by=args.group_by)

        print(
            f"tally — {t.records} record(s) from {t.runs} run(s): {t.recipients} "
            f"recipient(s), {t.units} recipient-touch(es)"
        )
        print(
            f"  agreement   {t.unanimous} unanimous · {t.split} split ({t.split_rate:.1%}) "
            f"· {t.contested} contested, no majority ({t.contested_rate:.1%})"
        )
        print(
            f"  send set    consensus {len(send_set(t, 'consensus'))}  <  majority "
            f"{len(send_set(t, 'majority'))}  <  single-pass {t.send_single_pass} "
            f"({t.primary_source!r})  <  any-pass {len(send_set(t, 'any'))}   "
            f"(of {t.recipients} recipient(s))"
        )
        if t.per_run_send_rate != (0.0, 0.0):
            lo, hi = t.per_run_send_rate
            print(f"  per-run send rate across the {t.runs} run(s): {lo:.1%} .. {hi:.1%}")
        print()
        print(
            "  This is re-judging variance across independent runs, NOT "
            "gtm_core.eval_calibration.flip_rate (rubric-order sensitivity within one "
            "run, <10% bar) — the two are not comparable."
        )
        for finding in t.findings:
            print(f"  FINDING: {finding}")

        write_tally(t.rows, args.out)
        print(f"\nwrote {len(t.rows)} row(s) -> {args.out}", file=sys.stderr)
        return 1 if t.failed else 0

    if args.cmd == "disposal":
        recs: list[Adjudication] = []
        for p in args.records:
            recs.extend(read_records(p))
        accounted: list[str] = []
        if args.accounted_for:
            accounted = [
                ln.strip()
                for ln in args.accounted_for.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")
            ]
        d = disposal_audit(recs, accounted_for=accounted, cap=args.cap)
        print(
            f"{d.total} record(s): send={d.send} re-angle={d.re_angle} drop={d.drop} "
            f"unscored={d.unscored}"
        )
        print(
            f"  repairable={d.repairable} stranded={d.stranded} "
            f"misfiled_drop={d.misfiled_drop} (unfiled {d.misfiled_unfiled}) "
            f"uncalibrated={d.uncalibrated}"
        )
        for f in d.findings:
            print(f"\nFINDING: {f}")
        if d.failed:
            print("\nFAIL — the non-send rows are not accounted for")
            return 1
        print("\nPASS")
        return 0

    if args.cmd == "repair-queue":
        queue = repair_queue(read_records(args.records), cap=args.cap)
        eligible = [c for c in queue if c.eligible]
        refused = [c for c in queue if not c.eligible]
        print(f"{len(eligible)} row(s) eligible for repair, {len(refused)} refused")
        for c in eligible:
            print(f"  REPAIR   attempt {c.record.repair_attempt} {c.record.email}")
        for c in refused:
            print(f"  REFUSED  {c.record.email}: {c.reason}")
        if args.out:
            write_records([c.record for c in eligible], args.out)
            print(f"\nwrote {args.out}")
        return 0

    if args.cmd == "check-complete":
        rows = _read_rows(args.csv)
        c = completeness(rows, read_records(args.records))
        print(
            f"{c.records} record(s) for {c.rows} row(s) — {c.scored} scored, {c.unscored} unscored"
        )
        if c.missing:
            print(f"\nFAIL — {len(c.missing)} row(s) have NO record at all:")
            for email in c.missing[:20]:
                print(f"  MISSING  {email}")
            if len(c.missing) > 20:
                print(f"  ... and {len(c.missing) - 20} more")
            print(
                "\nA batch that scored some of the list prints like one that scored all of "
                "it. That is why this check exists — do not proceed on a partial batch."
            )
            return 1
        print("PASS — every row has a record")
        return 0

    if args.cmd == "write-verdicts":
        rows = _read_rows(args.csv)
        records = read_records(args.records)
        c = completeness(rows, records)
        if c.missing:
            print(
                f"refusing to write verdicts: {len(c.missing)} of {c.rows} row(s) have no "
                f"record. Run `check-complete` — a partially-judged list must not be "
                f"written as a judged one.",
                file=sys.stderr,
            )
            return 1
        # Accumulate, never a dict comprehension. One address legitimately carries several
        # records — one per touch — and keying the other way silently kept whichever
        # happened to be last, so the verdict the enrollment gate read was the last
        # touch's rather than the list's. Worst wins: a drop found on touch 3 is a fact
        # about the row, and a send on touch 4 does not retract it.
        by_email: dict[str, list[Adjudication]] = {}
        for a in records:
            if a.unscored:
                continue
            by_email.setdefault((a.email or "").strip().lower(), []).append(a)

        # Imported here, not at module scope: `eval_calibration` imports this module, so a
        # top-level import would be a cycle. (`agent/mcp/judge/server.py` has no such
        # constraint and imports it normally.)
        from ..eval_calibration import is_calibrated

        calibrated = is_calibrated(args.profile) if args.profile else None
        written = 0
        for row in rows:
            recs = by_email.get((row.get("email") or "").strip().lower())
            if not recs:
                continue
            rec = worst_verdict(recs)
            # The judge's own columns. `verdict` is the researcher's and is never touched
            # here — one column with two writers is how "the judge ranks and never blocks"
            # became false of the artifact while staying true in three documents.
            row["judge_verdict"] = rec.verdict
            # A reason nobody can act on is the same as none — prefer the evidence phrase.
            row["judge_verdict_reason"] = rec.evidence or rec.note or rec.defect_class
            # The routing key, machine-readable. `judge_verdict_reason` is 76% email-body
            # text on real lists (it prefers the evidence phrase), so nothing downstream
            # could tell "wrong argument" from "wrong company" without this column.
            row["judge_defect_class"] = normalize_defect_class(rec.defect_class)
            row["judge_calibrated"] = "" if calibrated is None else str(bool(calibrated)).lower()
            written += 1

        # From the reader, not from `rows[0]`: a first row that happens to be missing an
        # optional key would silently truncate every other row's columns.
        fieldnames = list(_read_fieldnames(args.csv))
        for col in JUDGE_COLUMNS:
            if col not in fieldnames:
                fieldnames.append(col)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({c: (row.get(c) or "") for c in fieldnames})
        print(f"wrote {written} judge verdict(s) into {len(rows)} row(s) -> {args.out}")
        if calibrated is None:
            print(
                "  judge_calibrated is blank: no --profile, so nobody asked whether this "
                "judge has ever been measured against a human."
            )
        elif not calibrated:
            print(
                "  judge_calibrated=false: this judge has no PASSING score on a sealed "
                "holdout (never measured, or measured and failed). Its verdicts rank rows; "
                "they do not remove them."
            )
        return 0

    if args.cmd == "defect-report":
        from ..paths import resolve_content_root

        root = (args.content_root or resolve_content_root()).resolve()
        out = args.out.resolve()
        if root not in out.parents:
            print(
                f"REFUSED: {args.out} is outside the content root {root} — the report quotes "
                f"email bodies, which name people (§R9)",
                file=sys.stderr,
            )
            return 2
        groups = {source_name(p): read_records(p) for p in args.records}
        known = (
            [ln.strip() for ln in args.known_file.read_text(encoding="utf-8").splitlines()]
            if args.known_file
            else []
        )
        feedback = (
            [
                json.loads(ln)
                for ln in args.feedback.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            if args.feedback and args.feedback.is_file()
            else []
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(defect_report(groups, known=known, feedback=feedback), encoding="utf-8")
        print(
            f"defect report -> {out} ({len(groups)} source(s), {sum(len(g) for g in groups.values())} record(s))"
        )
        return 0

    if args.cmd == "regen-count":
        n = regeneration_count(args.spec)
        if n >= args.cap:
            print(
                f"REFUSED: {args.spec.name} has been regenerated {n} time(s) — the cap is {args.cap}. The verdict stands; re-target or drop.",
                file=sys.stderr,
            )
            return 1
        print(f"{args.spec.name}: regeneration {n} of {args.cap} — one more is allowed")
        return 0

    if args.cmd == "require-qa":
        ok, why = require_qa(args.spec, args.qa_dir, args.csv)
        print(("ok: " if ok else "REFUSED: ") + why)
        return 0 if ok else 1

    if args.cmd == "novel":
        records = read_records(args.records)
        known = _known_from(args)
        new = novel_classes(records, known)
        old = covered_classes(records, known)
        print(f"{len(records)} adjudication(s) against {len(set(known))} known rule(s)\n")
        print("CANDIDATE RULES — named by the read, covered by nothing:")
        for cls, n in new.items():
            print(f"  {n:4}  {cls}")
        if not new:
            print("  (none)")
        if old:
            print("\nINERT GATES — a rule covers these, and the read found them anyway:")
            for cls, n in old.items():
                print(f"  {n:4}  {cls}")
            print(
                "  A rule that exists and did not fire is a worse finding than a missing "
                "rule. Check the gate actually runs before writing another one."
            )
        return 0

    return 2
