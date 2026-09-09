from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..confine import ConfinementError, confined_output_path, confined_source_file
from ..paths import resolve_content_root
from .api import _active_constants, active_rules, lint_shotlist
from .capture import write_shotlist_markdown
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
        "--storyboard",
        type=Path,
        default=None,
        help=(
            "The run's storyboard.json, which is the only place a still's delta-edit depth is "
            "recorded. Supplying it turns on the reference-depth warning for every shot carrying "
            "`reference_images`; omitting it leaves that rule inert rather than guessing. NOT "
            "auto-discovered from a sibling path on purpose — a lint whose verdict changes "
            "because an unrelated file happens to sit next to the input is not reproducible."
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
    parser.add_argument(
        "--render-shotlist",
        action="store_true",
        help=(
            "Also write `<name>.shotlist.md` beside the JSON: the phone-readable shot list the "
            "operator actually reads on set. Generated, never edited — nothing reads it back. "
            "A capture contract that exists only in JSON is one that does not reach the shoot."
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

    storyboard = None
    if args.storyboard is not None:
        # A caller-supplied read path gets the same confinement as every other one: this is a
        # deterministic CLI a skill invokes with a path the brain composed.
        try:
            sb_path = confined_source_file(
                args.storyboard, content_root=resolve_content_root(), action="read a storyboard"
            )
            storyboard = json.loads(sb_path.read_text(encoding="utf-8"))
        except (ConfinementError, OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"path": str(args.path), "error": f"--storyboard: {exc}"}))
            return 2

    # The element library to check `elements` slugs against. The profile is not a flag — it is
    # read off the shot list's own location under the content root (`content/<profile>/…`), so
    # every real run gets the check without a new argument, and a fixture that lives outside the
    # root (tests, tripwire goldens) stays inert rather than being judged against the wrong
    # tenant. Before this, no production caller supplied `known_elements` and the rule was a
    # declared contract nobody ran. Found in review.
    known_elements: set[str] | None = None
    try:
        rel = args.path.resolve().relative_to(resolve_content_root().resolve())
        from ..elements import list_slugs

        known_elements = set(list_slugs(rel.parts[0]))
    except (ValueError, IndexError, OSError):
        known_elements = None

    errors, warnings = lint_shotlist(
        doc,
        voice_grade=args.voice_grade,
        voice_id=args.voice_id,
        disclosure_line=args.disclosure_line,
        storyboard=storyboard,
        known_elements=known_elements,
    )
    # Reported under its OWN key rather than merged into `errors`. Schema conformance and the
    # craft lint make different claims — the right SHAPE vs a good script — and a reader who
    # cannot tell which gate refused a file cannot tell what to fix.
    violations = schema_errors(doc) if args.schema else []

    # A failed twin write must not swallow the lint result: the findings were already computed,
    # and an operator who sees only a confinement error may retry without the flag and read the
    # file as clean. Report both, and still exit non-zero.
    shotlist_md = None
    shotlist_error = None
    if args.render_shotlist:
        try:
            shotlist_md = str(
                write_shotlist_markdown(
                    doc,
                    confined_output_path(args.path, content_root=resolve_content_root()),
                )
            )
        except (ConfinementError, OSError) as exc:
            shotlist_error = f"--render-shotlist: {exc}"

    payload = {
        "path": str(args.path),
        "ok": not errors and not violations,
        "errors": errors,
        "warnings": warnings,
        "schema": SCHEMA_PATH.name if args.schema else None,
        "schema_errors": violations,
    }
    if shotlist_md is not None:
        payload["shotlist_md"] = shotlist_md
    if shotlist_error is not None:
        payload["shotlist_error"] = shotlist_error
    print(json.dumps(payload, indent=2))
    if shotlist_error is not None:
        return 2
    return 1 if (errors or violations) else 0
