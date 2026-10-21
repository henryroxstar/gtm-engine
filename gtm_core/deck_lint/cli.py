from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api import lint, report, verify_manifest
from .model import ERROR
from .parse import parse_slides
from .rules_claims import guardrails_from
from .rules_questions import question_bank, question_slides
from .theme import lint_theme


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deck-lint")
    parser.add_argument("paths", nargs="*", type=Path, help="one or more slides.md")
    parser.add_argument(
        "--theme",
        type=Path,
        help="deck-theme dir (e.g. .engine/deck-theme) — runs the D9 render-weight audit. "
        "May be passed with or without slides.md paths.",
    )
    parser.add_argument(
        "--dossier",
        type=Path,
        action="append",
        default=None,
        help="account dossier (questions + DON'Ts). Repeatable — pass the dossier spec AND the\n"
        "deck's approved-questions file, which is where an extension to the bank is recorded.",
    )
    parser.add_argument("--inputs", type=Path, help="deck inputs/ dir — the quote corpus")
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--template", help="A1–A10 · sets D7 severity")
    parser.add_argument("--allow-appendix", action="store_true")
    parser.add_argument("--strict", action="store_true", help="warnings fail too")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    if not args.paths and not args.theme:
        parser.error("pass one or more slides.md paths, --theme, or both")

    bank = None
    rails: list[tuple[str, str]] = []
    for source in args.dossier or []:
        bank = (bank or []) + question_bank(source)
        rails += guardrails_from(source)
    corpus = None
    if args.inputs:
        files = args.inputs.rglob("*.md") if args.inputs.is_dir() else [args.inputs]
        corpus = "\n".join(p.read_text(encoding="utf-8") for p in files)
        # A deck may ask something the dossier never listed — but only by writing it into
        # its own inputs first, where a reviewer sees it. That is the difference between
        # an extension and an invention.
        if bank is not None:
            bank = bank + [
                ln.strip("-*# ").strip() for ln in corpus.split("\n") if ln.strip().endswith("?")
            ]

    failed = False
    payload: dict[str, list[dict]] = {}
    for path in args.paths:
        if not path.exists():
            print(f"✗ no such file: {path}", file=sys.stderr)
            failed = True
            continue
        text = path.read_text(encoding="utf-8")
        findings = lint(
            text,
            bank=bank,
            guardrails=rails,
            corpus=corpus,
            minutes=args.minutes,
            template=args.template,
            allow_appendix=args.allow_appendix,
        )
        if args.as_json:
            payload[str(path)] = [f.__dict__ for f in findings]
        else:
            parsed = parse_slides(text)
            report(path, findings, verify_manifest(parsed), question_slides(parsed))
        if any(f.severity == ERROR for f in findings) or (args.strict and findings):
            failed = True

    if args.theme:
        if not args.theme.exists():
            print(f"✗ no such theme dir: {args.theme}", file=sys.stderr)
            failed = True
        else:
            theme_findings = lint_theme(args.theme)
            if args.as_json:
                payload[f"theme:{args.theme}"] = [f.__dict__ for f in theme_findings]
            else:
                report(args.theme, theme_findings, [])
            if any(f.severity == ERROR for f in theme_findings) or (args.strict and theme_findings):
                failed = True

    if args.as_json:
        print(json.dumps(payload, indent=2))
    elif failed:
        print(
            "\n  D1 is an estimate — scripts/deck_fit_probe.mjs measures the real canvas and"
            "\n  is the tiebreaker. A rule that is genuinely wrong about a slide can be"
            "\n  suppressed with <!-- lint-ok D4: reason --> inside that slide."
        )
    return 1 if failed else 0
