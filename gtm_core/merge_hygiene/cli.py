from __future__ import annotations

from .signal_terms import SIGNAL_TOPIC_TERMS, signal_is_event, signal_on_topic


def main(argv: list[str] | None = None) -> int:
    """Bucket a prospect CSV by whether each row's clause can open a given campaign body.

    Deliberately runnable BEFORE any copy exists. The merge-render linter reports the same
    defect, but only once a spec is written — by which point the body has usually already
    been softened to fit the weakest clause in the list, which is the wrong repair. This
    decides LIST COMPOSITION: re-research the row, or send it a sequence about something
    it is actually about.

    Reads the ``signal_clause`` column (the sendable clause), never ``why_now`` (the
    multi-fact research note) — the two are confusable and only one ever renders.
    """
    import argparse
    import csv
    import sys

    ap = argparse.ArgumentParser(prog="python -m gtm_core.merge_hygiene")
    ap.add_argument("csv_path", help="prospect CSV carrying a signal_clause column")
    ap.add_argument(
        "--terms",
        help="comma-separated campaign vocabulary (default: the agentic-AI set)",
    )
    ap.add_argument(
        "--column", default="signal_clause", help="clause column (default: signal_clause)"
    )
    ap.add_argument(
        "--include-suppressed",
        action="store_true",
        help="also judge rows already carrying a suppression reason",
    )
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    terms = (
        tuple(t.strip() for t in args.terms.split(",") if t.strip())
        if args.terms
        else SIGNAL_TOPIC_TERMS
    )
    with open(args.csv_path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not args.include_suppressed:
        rows = [r for r in rows if not (r.get("suppression") or "").strip()]
    if not rows:
        print("no live rows", file=sys.stderr)
        return 2

    off = [r for r in rows if not signal_on_topic(r.get(args.column) or "", terms)]
    static = [
        r
        for r in rows
        if signal_on_topic(r.get(args.column) or "", terms)
        and not signal_is_event(r.get(args.column) or "")
    ]
    print(f"{len(rows)} live rows in {args.csv_path}")
    print(f"  on topic     {len(rows) - len(off):>4}  ({(len(rows) - len(off)) / len(rows):.0%})")
    print(f"  OFF TOPIC    {len(off):>4}  ({len(off) / len(rows):.0%})  <- re-research or suppress")
    print(f"  no event verb{len(static):>4}  ({len(static) / len(rows):.0%})  (advisory)")
    for r in off:
        print(f"    {(r.get('email') or '?').strip():<40} {(r.get(args.column) or '')[:70]}")
    return 1 if off else 0
