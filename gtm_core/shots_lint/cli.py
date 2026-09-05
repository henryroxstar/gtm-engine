from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api import _active_constants, active_rules, lint_shotlist
from .schema import SCHEMA_PATH, schema_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="uv run python -m gtm_core.shots_lint",
        description="Deterministic craft lint for a <slug>.shots.json (free, pre-render).",
    )
    parser.add_argument(
        "path", type=Path, nargs="?", help="Path to the shot list JSON (omit with --rules)"
    )
    parser.add_argument(
        "--rules",
        action="store_true",
        help=(
            "Print the plan-time rules this linter enforces, and their constants, then exit. "
            "Read-only and free. Callers RELAY this instead of restating it in prose — a "
            "hand-copied summary of the rule set is what went stale on 2026-08-28, so a script "
            "was written to a target the linter had already moved past."
        ),
    )
    parser.add_argument(
        "--voice-grade",
        default=None,
        help=(
            "The brand kit's recorded clone grade for the engine that will actually render the "
            "spoken shots — identity.heygen_voice_grade for a HeyGen presenter, "
            "identity.voice_grade for a Higgsfield one. Two providers means two grades, and "
            "checking the wrong one is checking nothing. Omit to skip the check; pass an empty "
            "string to mean 'the kit was read and records no grade', which fails closed."
        ),
    )
    parser.add_argument(
        "--voice-id",
        default=None,
        help=(
            "The brand kit's identity.voice_id — the voice that will actually speak the "
            "[SPOKEN] lines. Omit to skip the check; pass an empty string to mean 'the kit was "
            "read and records no voice', which refuses any spoken line the render would leave "
            "silent. A shot list may answer for itself with a top-level `vo_source`."
        ),
    )
    parser.add_argument(
        "--disclosure-line",
        default=None,
        help=(
            "The brand kit's [disclosure].line — the EU AI Act Art. 50 line the finished render "
            "will carry. A shot list's `synthetic_disclosure` DECLARES that line, so passing it "
            "here checks the declaration is the line rather than a handle kind or a paraphrase, "
            "before any render spend. Omit to skip the match; pass an empty string to mean 'the "
            "kit was read and configures no line', which fails closed."
        ),
    )
    parser.add_argument(
        "--no-schema",
        dest="schema",
        action="store_false",
        help=(
            f"Skip conformance against {SCHEMA_PATH.name}, which otherwise runs by DEFAULT. The "
            "default is the point: that schema declared itself this pipeline's contract and "
            "nothing ever ran it, so 11 of 14 committed shot lists had drifted off it unnoticed "
            "by 2026-09-03. An opt-in check is a check nobody opts into. Use this only to lint "
            "the craft rules on a document you already know is off-contract."
        ),
    )
    args = parser.parse_args(argv)

    if args.rules:
        print(json.dumps({"rules": active_rules(), "constants": _active_constants()}, indent=2))
        return 0

    if args.path is None:
        parser.error("a shot list path is required (or pass --rules)")

    try:
        doc = json.loads(args.path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(json.dumps({"path": str(args.path), "error": f"unreadable: {exc}"}))
        return 2
    except json.JSONDecodeError as exc:
        print(json.dumps({"path": str(args.path), "error": f"invalid JSON: {exc}"}))
        return 2

    errors, warnings = lint_shotlist(
        doc,
        voice_grade=args.voice_grade,
        voice_id=args.voice_id,
        disclosure_line=args.disclosure_line,
    )
    # Reported under its OWN key rather than merged into `errors`. Schema conformance and the
    # craft lint make different claims — the right SHAPE vs a good script — and a reader who
    # cannot tell which gate refused a file cannot tell what to fix.
    violations = schema_errors(doc) if args.schema else []
    print(
        json.dumps(
            {
                "path": str(args.path),
                "ok": not errors and not violations,
                "errors": errors,
                "warnings": warnings,
                "schema": SCHEMA_PATH.name if args.schema else None,
                "schema_errors": violations,
            },
            indent=2,
        )
    )
    return 1 if (errors or violations) else 0
