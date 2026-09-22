from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api import coverage_map, lint, lint_skill, report
from .calibrate import measure, render
from .catalog import dimensions
from .model import ADVISORY, ERROR
from .parse import UnparseableDesign, parse_sections


def _print_dimensions() -> int:
    """`--dimensions` — the coverage taxonomy, read-only. Lints nothing, always exits 0."""
    for dimension in dimensions():
        print(f"{dimension.id}  {dimension.name}  [{dimension.severity}] ({dimension.source})")
        print(f"    asks:      {dimension.question}")
        print(f"    answered:  {dimension.satisfied_by or 'a section of its own'}")
    return 0


def _calibrate(pattern: str) -> int:
    """`--calibrate` — measure the rule set against a corpus, stratified by provenance.

    A glob that matches nothing FAILS rather than reporting a clean corpus of zero: a
    confident pass over documents nobody found is the failure this whole harness exists for.
    """
    try:
        paths = sorted(Path().glob(pattern))
    except (NotImplementedError, ValueError) as exc:
        # An absolute pattern raises rather than matching. Refusing beats a traceback from a
        # command whose whole job is to report a measurement legibly.
        print(f"✗ {pattern}: not a relative glob ({exc})", file=sys.stderr)
        return 1
    if not paths:
        print(f"✗ no documents matched: {pattern}", file=sys.stderr)
        return 1
    measurement = measure(paths)
    print(render(measurement))
    return 0 if measurement.holds else 1


def _blocks(findings: list, strict: bool) -> bool:
    """Whether this document's findings should fail the run.

    `--strict` promotes warnings and deliberately does NOT promote advisories. SD6-SD9 are
    judgement calls whose whole premise is that the operator knows more than the rule does;
    a `--strict` that could fail a build on one would hand the decision back to the linter
    and make the severity meaningless.
    """
    gradeable = [f for f in findings if f.severity != ADVISORY]
    return any(f.severity == ERROR for f in gradeable) or (strict and bool(gradeable))


def _read(path: Path) -> str | None:
    """The document's text, or None after printing a legible refusal.

    `UnparseableDesign` was handled from the start and these were not, so a directory, a
    non-UTF-8 byte or a vanished file produced a Python traceback from a gate three skills
    invoke — an agent reading that cannot tell "this document is wrong" from "the linter
    broke". Every way a document can fail to be read now refuses in the same shape.
    """
    try:
        return path.read_text(encoding="utf-8")
    except IsADirectoryError:
        print(f"✗ {path}: is a directory, not a design document", file=sys.stderr)
    except UnicodeDecodeError:
        print(f"✗ {path}: is not UTF-8 text — a design document is markdown", file=sys.stderr)
    except OSError as exc:
        print(f"✗ {path}: {exc.strerror or exc}", file=sys.stderr)
    return None


def _lint_designs(args, payload: dict[str, list[dict]]) -> bool:
    """Lint each design path. Returns True when anything failed."""
    failed = False
    for path in args.paths:
        if not path.exists():
            print(f"✗ no such file: {path}", file=sys.stderr)
            failed = True
            continue
        text = _read(path)
        if text is None:
            failed = True
            continue
        try:
            findings = lint(text)
            sections = parse_sections(text)
        except UnparseableDesign as exc:
            # Refuse, never skip. A document that lints zero sections reports clean, and a
            # confident clean run on a file nobody could read is the worse outcome.
            print(f"✗ {path}: {exc}", file=sys.stderr)
            failed = True
            continue
        if args.as_json:
            payload[str(path)] = [f.__dict__ for f in findings]
        else:
            report(path, findings, coverage_map(sections), examined=len(sections))
        failed = _blocks(findings, args.strict) or failed
    return failed


def _lint_skills(args, payload: dict[str, list[dict]]) -> bool:
    """Run the SD11 audit over each `--skill` body. Returns True when anything failed."""
    failed = False
    for path in args.skill or []:
        if not path.exists():
            print(f"✗ no such skill body: {path}", file=sys.stderr)
            failed = True
            continue
        text = _read(path)
        if text is None:
            failed = True
            continue
        findings = lint_skill(text)
        if args.as_json:
            payload[f"skill:{path}"] = [f.__dict__ for f in findings]
        else:
            report(path, findings, [])
        failed = _blocks(findings, args.strict) or failed
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="design-lint")
    parser.add_argument(
        "paths", nargs="*", type=Path, help="one or more solution-design markdown files"
    )
    parser.add_argument(
        "--skill",
        type=Path,
        action="append",
        default=None,
        help="a skill body (body_template.md) — runs the SD11 contradictory-guardrail audit. "
        "Repeatable. May be passed with or without design paths.",
    )
    parser.add_argument(
        "--calibrate",
        metavar="GLOB",
        help="measure the whole rule set against a corpus of real designs, stratified by "
        "provenance. Prints counts per stratum and the conformant-stratum error count "
        "that is the ERROR bar. Never prints a filename.",
    )
    parser.add_argument(
        "--dimensions",
        action="store_true",
        help="print the coverage taxonomy — the question each dimension asks and the section "
        "of our structure that answers it. Read-only; exits 0 and lints nothing. This is what "
        "an upstream skill (solution-discovery) checks its question bank against, so the "
        "questions a design must answer are sourced before the design is written.",
    )
    parser.add_argument("--strict", action="store_true", help="warnings fail too")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    if args.dimensions:
        return _print_dimensions()
    if args.calibrate:
        return _calibrate(args.calibrate)
    if not args.paths and not args.skill:
        parser.error(
            "pass one or more design paths, --skill, --calibrate, --dimensions, or a combination"
        )

    payload: dict[str, list[dict]] = {}
    failed = _lint_designs(args, payload)
    failed = _lint_skills(args, payload) or failed

    if args.as_json:
        print(json.dumps(payload, indent=2))
    elif failed:
        print(
            "\n  Errors block delivery. Warnings do not, but they are graded: read them,"
            "\n  then either fix or say why not, and where one is genuinely wrong about a"
            "\n  document suppress it with <!-- lint-ok SD4: reason --> in that section."
            "\n  Judgement calls (~) are neither — they block nothing, --strict does not"
            "\n  promote them, and they take no lint-ok. Three tiers, three answers: two"
            "\n  that contradict each other train a reader to ignore both."
        )
    return 1 if failed else 0
