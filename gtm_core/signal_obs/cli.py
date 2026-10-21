"""`python -m gtm_core.signal_obs`: check, due, extract, precision, review, repair.

Every command takes ``--profile`` and ``--product`` and goes through ``run_scope.require``; it
prints the registry file it read. Nothing writes unless it says so: ``extract`` appends
observations, ``precision`` only reads, ``due --write-manifest`` writes the allow-list, ``review --apply`` and
``repair --apply`` are the only writers behind an explicit flag.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

from .. import run_scope
from ..confine import ConfinementError, confined_output_path
from ..signal_sources import sources_dir_for
from . import due, extract, precision, registry, repair, review
from .members import parse_brain_list
from .switch import DISABLED_MESSAGE, SwitchClosed, sources_enabled


def _run_id(given: str | None) -> str:
    """A run id that is this run's own: a constant one would merge every run's writer name."""
    return given or datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%f")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _dropped(rep: extract.ExtractReport) -> str:
    """What the extractor quietly left out, so a short list is never a silent one."""
    parts = [
        (rep.refused_short, "name too short to match safely", "names too short to match safely"),
        (rep.unverified, "name the page does not actually say", "names the page does not say"),
        (
            rep.domain_dropped,
            "web address the page does not carry",
            "web addresses not on the page",
        ),
    ]
    said = [f"{n} {one if n == 1 else many}" for n, one, many in parts if n]
    return f" Left out: {', '.join(said)}." if said else ""


def _report_line(rep: extract.ExtractReport, title: str) -> str:
    if rep.status in ("baseline", "diff"):
        head = (
            f"This was the first look at {title}: {_plural(rep.members, 'member')} already on "
            "the list recorded as they are, with no one counted as newly joined."
            if rep.baseline
            else f"{title}: {_plural(rep.members, 'member')} on the page, {rep.joins} newly joined."
        )
        tail = (
            f" {_plural(rep.unresolved, 'name')} could not be matched to an account and wait for review."
            if rep.unresolved
            else ""
        )
        return head + tail + _dropped(rep)
    return f"{title}: {rep.status}. {rep.reason}"


def _load_json_object(path: str, what: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{what} file {path} cannot be read ({type(exc).__name__})") from None
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError(f"{what} file must be a JSON object of name to domain")
    return data


def _cmd_extract(args) -> int:
    proposed = candidates = None
    try:
        if args.brain_list:
            proposed = parse_brain_list(Path(args.brain_list).read_text(encoding="utf-8"))
        if args.candidates:
            candidates = _load_json_object(args.candidates, "candidates")
    except (OSError, ValueError) as exc:
        print(str(exc))
        return 2
    rep = extract.run_extract(
        args.profile,
        args.source,
        product=args.product,
        run_id=_run_id(args.run_id),
        proposed=proposed,
        candidates=candidates,
        rebaseline=args.rebaseline,
    )
    reg = registry.load_registry(args.profile, args.product) if rep.status != "refused" else None
    source = reg.by_id.get(args.source) if reg else None
    print(_report_line(rep, source.title if source else args.source))
    for problem in reg.grading_problems if reg else ():
        print(f"Grading: {problem}")
    return 0 if rep.status in ("baseline", "diff") else 2


def _cmd_review(args) -> int:
    sheet = args.apply or args.plan
    if not sheet:
        entries = review.pending(args.profile, args.product)
        text = review.render_sheet(entries)
        if args.out:
            target = confined_output_path(args.out, content_root=review.obs_dir(args.profile))
            if target.exists():
                raise ConfinementError(f"{target} already exists; a sheet never overwrites a file")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            print(f"{_plural(len(entries), 'name')} on the sheet, written to {args.out}")
        else:
            sys.stdout.write(text)
        return 0
    text = Path(sheet).read_text(encoding="utf-8")
    rep = review.apply_sheet(
        args.profile,
        text,
        product=args.product,
        run_id=_run_id(args.run_id),
        apply=bool(args.apply),
    )
    for d in rep.decisions:
        print(f"- {d.name}: {d.choice}" + (f" -> {d.account_key}" if d.account_key else ""))
    for e in rep.errors:
        print(f"Refused: {e}")
    for n in rep.notes:
        print(n)
    if rep.errors:
        return 2
    print(
        f"{_plural(rep.written, 'decision')} written."
        if args.apply
        else "Plan only; add --apply to write."
    )
    return 0


def _cmd_repair(args) -> int:
    plan = repair.run_repair(args.profile, args.product, args.shard, apply=bool(args.apply))
    if plan.applied:
        print(f"Removed the cut-off last line ({plan.drop_bytes} bytes) from {args.shard}.")
    elif plan.drop_bytes:
        print(
            f"Would remove the cut-off last line ({plan.drop_bytes} bytes) from {args.shard}. Add --apply."
        )
    else:
        print(f"Nothing changed: {plan.reason}")
    return 0 if (plan.applied or plan.drop_bytes or not plan.reason.startswith("damage")) else 2


def _cmd_due(args) -> int:
    report = due.due_sources(args.profile, args.product)
    due.print_report(report)
    if args.write_manifest:
        run_id = _run_id(args.run_id)
        path = due.write_due_manifest(args.profile, args.product, run_id=run_id)
        print(f"Manifest: {path}" if path else "Nothing is due, so no manifest was written.")
    return 0


def _cmd_precision(args) -> int:
    reg = registry.load_registry(args.profile, args.product)
    got = precision.measure(args.profile)
    print(f"Registry: {reg.path}. Graded files: {', '.join(got.files) or 'none'}.")
    for s in reg.sources:
        t = got.by_source.get(s.id)
        if t is None:
            note = f", {got.ungraded[s.id]} ungraded" if s.id in got.ungraded else ""
            print(f"- {s.id}: stated {s.precision or 'not sampled'}; no graded members{note}.")
            continue
        verdict = (
            "too few graded to judge"
            if t.graded < registry.MIN_SAMPLE
            else f"{'below' if t.rate < registry.PRECISION_BAR else 'at or above'} "
            f"{registry.PRECISION_BAR:.0%}"
        )
        print(
            f"- {s.id}: stated {s.precision or 'not sampled'}; graded {t.text()} ({t.rate:.0%}), {verdict}."
        )
    unknown = sorted(set(got.by_source) - set(reg.by_id))
    for sid in unknown:
        print(
            f"- {sid}: {got.by_source[sid].text()} graded, but no source has this id in the registry."
            " Retire a source by moving expires_on into the past, not by deleting its row."
        )
    for problem in got.problems:
        print(f"Problem: {problem}")
    return 2 if got.problems or unknown else 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m gtm_core.signal_obs", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(name: str, **kw):
        sp = sub.add_parser(name, **kw)
        sp.add_argument("--profile", required=True)
        sp.add_argument("--product", default=None, help="the product this run is for")
        return sp

    common("check", help="validate the registry and preview members")
    d = common("due", help="which sources need a capture")
    d.add_argument("--write-manifest", action="store_true")
    d.add_argument("--run-id", default=None)
    e = common("extract", help="record who a source's latest capture lists")
    e.add_argument("--source", required=True)
    e.add_argument("--run-id", default=None)
    e.add_argument("--brain-list", default=None, help="JSON names the brain proposed")
    e.add_argument("--candidates", default=None, help="JSON object of name to candidate domain")
    e.add_argument("--rebaseline", action="store_true")
    common("precision", help="graded members against each source's stated precision (read-only)")
    r = common("review", help="the unmatched-names sheet")
    r.add_argument("--plan", default=None, help="show what a filled sheet would do")
    r.add_argument("--apply", default=None, help="write the decisions on a filled sheet")
    r.add_argument("--out", default=None)
    r.add_argument("--run-id", default=None)
    f = common("repair", help="truncate a cut-off last line of one shard")
    f.add_argument("--shard", required=True)
    f.add_argument("--apply", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not sources_enabled():
        print(DISABLED_MESSAGE)
        return 0
    try:
        if args.cmd == "check":
            return registry.check(
                args.profile, args.product, sources_dir=sources_dir_for(args.profile)
            )
        return {
            "due": _cmd_due,
            "extract": _cmd_extract,
            "precision": _cmd_precision,
            "review": _cmd_review,
            "repair": _cmd_repair,
        }[args.cmd](args)
    except run_scope.ScopeError as exc:
        print(f"Refused: {exc}")
        return 2
    except SwitchClosed:
        print(DISABLED_MESSAGE)
        return 0
    except (ValueError, OSError, registry.RegistryError, ConfinementError) as exc:
        print(f"Refused: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
