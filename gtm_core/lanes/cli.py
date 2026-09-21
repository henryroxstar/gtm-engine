"""``python -m gtm_core.prospects lanes <verb>`` — route, hold-apply, suggest-rules."""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import re
import sys
from collections import Counter
from pathlib import Path

from ..adjudication import read_records
from ..prospects_consolidate.paths import _sequences_dir
from . import decisions as dec
from .context import POLICY_FILE, load_context
from .model import PROTECTIVE_HOLD_TRIGGERS
from .router import route, summary, write_lanes
from .sheet import write_sheet

#: Judge records older than this route nothing: a row re-researched since the sweep would
#: get last month's verdict. The date comes from the file name (``…-YYYY-MM-DD.jsonl``)
#: or, failing that, its mtime.
DEFAULT_MAX_RECORD_AGE_DAYS = 14
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

#: ``suggest-rules`` proposes a policy line only past this many unanimous decisions.
RULE_THRESHOLD = 10


def record_date(path: Path) -> datetime.date:
    m = _DATE_RE.search(path.name)
    if m:
        return datetime.date.fromisoformat(m.group(1))
    return datetime.date.fromtimestamp(path.stat().st_mtime)


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _cli_route(args) -> int:
    as_of = args.as_of or datetime.date.today()
    stale = [
        f"{p.name} ({(as_of - record_date(p)).days}d old)"
        for p in args.records
        if (as_of - record_date(p)).days > args.max_record_age_days
    ]
    if stale and not args.allow_stale_records:
        print(
            f"REFUSED: judge records older than {args.max_record_age_days} day(s) would route "
            f"today's rows on last month's verdicts: {', '.join(stale)}. Re-judge, or pass "
            f"--allow-stale-records to route on them knowingly.",
            file=sys.stderr,
        )
        return 2
    rows = _read_csv(args.csv)
    records = [r for p in args.records for r in read_records(p)]
    ctx = load_context(args.profile, as_of=as_of)
    prior = dec.read_decisions(dec.decisions_path(args.profile))
    previous = dec.read_state(dec.state_path(args.profile))
    result = route(
        rows,
        records,
        ctx,
        source=", ".join(p.name for p in args.records),
        decisions=prior,
        previous=previous,
        unattended=args.unattended,
    )
    print(summary(result))
    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0
    stamp = args.stamp or as_of.isoformat()
    seq_dir = args.out_dir or _sequences_dir(args.profile, None)
    paths = write_lanes(result, seq_dir, stamp)
    hold = dec.write_hold_csv(result.lane("hold"), dec.hold_path(args.profile, stamp), prior)
    auto = [
        {"email": r.email, "trigger": r.trigger, "decided": r.decided}
        for r in result.routed
        if r.decided
    ]
    sheet = write_sheet(hold, hold.with_suffix(".html"), stamp=stamp, auto=auto)
    dec.write_state(result, dec.state_path(args.profile), stamp)
    # Re-stamp `ready-to-load.csv` right now (PS2) rather than waiting for the next
    # `consolidate` sweep to notice the new state file — a deferred import, matching
    # `consolidate._stamp_lanes`'s own deferred import of `lanes.decisions`, since the two
    # packages otherwise have no reason to import each other at module load time.
    from ..prospects_consolidate.consolidate import restamp_ready_to_load

    restamp_ready_to_load(args.profile)
    print()
    for lane, path in paths.items():
        print(f"  {lane:<12} {path}")
    print(f"  hold queue   {hold}")
    print(f"  hold sheet   {sheet}  (open it; decide by group; download; then hold-apply)")
    return 0


def _cli_hold_apply(args) -> int:
    entries = dec.read_filled(args.decisions)
    prior = dec.read_decisions(dec.decisions_path(args.profile))
    plan = dec.plan_apply(entries, prior)
    print(plan.render())
    if not args.apply:
        print("\n(plan only — pass --apply to write the ledgers)")
        return 0
    stamp = args.stamp or dec.today_stamp()
    out = dec.apply(plan, args.profile, stamp)
    print(
        f"\napplied: {out['suppressed']} suppression row(s) added ({out['suppression_skipped']} "
        f"already covered), {out['recorded']} decision(s) recorded → the next `lanes route` honours them"
    )
    return 0


def _cli_suggest_rules(args) -> int:
    """Propose ``[auto]`` lines for the tenant's lane policy. Never writes the file, never
    proposes ``suppress``."""
    rows = dec._read_jsonl(dec.decisions_path(args.profile))
    by_trigger: dict[str, Counter] = {}
    for r in rows:
        if r.get("trigger"):
            by_trigger.setdefault(r["trigger"], Counter())[r.get("decision", "")] += 1
    proposals = []
    for trigger, counts in sorted(by_trigger.items()):
        total = sum(counts.values())
        choice, n = counts.most_common(1)[0]
        if trigger in PROTECTIVE_HOLD_TRIGGERS:
            print(
                f"  {trigger}: {total} unanimous decisions — protective hold triggers are never proposed as policy"
            )
            continue
        if total >= args.threshold and n == total and choice in ("generic", "salvage"):
            proposals.append(f'{trigger} = "{choice}"')
        elif total >= args.threshold and n == total and choice == "suppress":
            print(
                f"  {trigger}: {total} unanimous suppress decisions — suppress is never proposed as policy"
            )
    if not proposals:
        print(f"no trigger has {args.threshold}+ unanimous generic/salvage decisions yet")
        return 0
    print(f"add to profiles/{args.profile}/knowledge/{POLICY_FILE} (the tool never writes it):\n")
    print("[auto]")
    for line in proposals:
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gtm_core.lanes", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("route", help="route every pooled row to exactly one lane")
    rp.add_argument("--profile", required=True)
    rp.add_argument("--csv", required=True, type=Path, help="the pooled list (ready-to-load.csv)")
    rp.add_argument("--records", required=True, type=Path, nargs="+", help="judge JSONL file(s)")
    rp.add_argument("--as-of", type=datetime.date.fromisoformat, default=None)
    rp.add_argument("--stamp", default="", help="date stamp for output names (default: as-of)")
    rp.add_argument(
        "--out-dir", type=Path, default=None, help="where lane CSVs go (default: sequences/)"
    )
    rp.add_argument("--max-record-age-days", type=int, default=DEFAULT_MAX_RECORD_AGE_DAYS)
    rp.add_argument("--allow-stale-records", action="store_true")
    rp.add_argument("--dry-run", action="store_true", help="print the summary; write nothing")
    rp.add_argument(
        "--unattended", action="store_true", help="fail-closed by routing generic/repair to hold"
    )

    hp = sub.add_parser("hold-apply", help="apply a filled hold queue (plan by default)")
    hp.add_argument("--profile", required=True)
    hp.add_argument(
        "--decisions", required=True, type=Path, help="filled hold-<date>.csv or exported .jsonl"
    )
    hp.add_argument("--stamp", default="")
    hp.add_argument("--apply", action="store_true", help="write the ledgers (default: plan only)")

    shp = sub.add_parser("hold-sheet", help="(re)build the HTML review sheet from a hold CSV")
    shp.add_argument("--profile", required=True)
    shp.add_argument("--hold", required=True, type=Path, help="hold-<date>.csv written by route")
    shp.add_argument(
        "--bodies", type=Path, default=None, help="JSON {seat: email text} to show per group"
    )
    shp.add_argument("--out", type=Path, default=None)

    sp = sub.add_parser("suggest-rules", help="propose lane-policy lines from repeated decisions")
    sp.add_argument("--profile", required=True)
    sp.add_argument("--threshold", type=int, default=RULE_THRESHOLD)

    args = p.parse_args(argv)
    if args.cmd == "route":
        return _cli_route(args)
    if args.cmd == "hold-apply":
        return _cli_hold_apply(args)
    if args.cmd == "hold-sheet":
        bodies = json.loads(args.bodies.read_text(encoding="utf-8")) if args.bodies else None
        stamp = _DATE_RE.search(args.hold.name)
        out = write_sheet(
            args.hold,
            args.out or args.hold.with_suffix(".html"),
            stamp=stamp.group(1) if stamp else dec.today_stamp(),
            bodies=bodies,
        )
        print(f"hold sheet -> {out}")
        return 0
    return _cli_suggest_rules(args)


if __name__ == "__main__":
    sys.exit(main())
