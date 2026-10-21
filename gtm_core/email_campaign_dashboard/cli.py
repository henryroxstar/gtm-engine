from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..page_inputs_guard import RefusedWrite
from .fingerprint import code_fingerprint, code_note
from .freshness import check_all_pages, refresh_pages
from .model import LaneStateUnreadable
from .render import check_fresh, render_dashboard
from .scope import MODES


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m gtm_core.email_campaign_dashboard",
        description="Render the email-campaign status page.",
    )
    ap.add_argument("--profile", required=True)
    ap.add_argument("--content-root", default=None)
    ap.add_argument("--no-stubs", action="store_true", help="do not rewrite the retired pages")
    ap.add_argument(
        "--scope",
        default=None,
        choices=MODES,
        help=(
            "which campaigns the page is about. 'campaign' needs --campaign and renders "
            "exactly those; 'open' renders the campaigns whose manifest status is active; "
            "'all' is the profile-wide rollup. Defaults to 'campaign' when --campaign is "
            "given and 'all' otherwise, so existing callers are unchanged. A tile that "
            "cannot be aggregated honestly over the scope refuses to render rather than "
            "reporting one campaign's figure under the set's label."
        ),
    )
    ap.add_argument(
        "--campaign",
        default=None,
        help=(
            "the slugs for --scope campaign: ONE, or several as a comma-separated list. "
            "Every slug must have a manifest; unknown slugs are refused rather than "
            "silently falling back to the rollup, which is a different campaign's numbers."
        ),
    )
    ap.add_argument(
        "--refresh-all",
        action="store_true",
        help=(
            "re-render every page that already exists, each under its own recorded scope. "
            "Only the profile rollup is refreshed automatically, so the scoped pages go "
            "stale silently — a stale page renders identically to a current one. This is the "
            "one remedy for a red --check-fresh. A page whose campaign no longer resolves is "
            "listed as a retired candidate and left as it is. Exits successfully only when --check-fresh "
            "would then be green: non-zero if any page failed or cannot be re-rendered (no "
            "recorded scope, a damaged sidecar, a deleted page file, a symlink), naming each "
            "with the command that clears it, and non-zero if the sending figures are too old "
            "(a re-render cannot refresh them). Cannot be combined with --check-fresh."
        ),
    )
    ap.add_argument(
        "--check-fresh",
        action="store_true",
        help=(
            "do not render — report whether the pages are still built from what is on disk "
            "now, and exit non-zero if not. With no --scope and no --campaign this checks "
            "EVERY page under the profile, because the rollup is the only one anything "
            "re-renders on its own and the scoped pages go stale silently. It also goes red "
            "when the sending figures are older than the limit, which no amount of "
            "re-digesting can see; that holds for a named --scope or --campaign too, judged "
            "by the figures that page itself recorded. A named page is judged by the same "
            "rules as the all-pages form. --campaign goes with --scope campaign only and "
            "must name a campaign that has a manifest. The remedy is --refresh-all, then "
            "check again. Cannot be combined with --refresh-all."
        ),
    )
    args = ap.parse_args(argv)
    if args.check_fresh and args.refresh_all:
        ap.error("--check-fresh only reads and --refresh-all writes; run one, then the other")
    root = Path(args.content_root) if args.content_root else None
    try:
        return _run(args, root)
    except FileNotFoundError as err:
        print(f"ABORTED: {err}", file=sys.stderr)
        return 1
    except RefusedWrite as err:
        print(f"ABORTED: {err}", file=sys.stderr)
        return 1
    except LaneStateUnreadable as err:
        # Every lane-derived figure on the page reads that one file. A page rendered without it
        # is a page of numbers that are quietly short, which is worse than no page.
        print(
            f"ABORTED: the sorted list could not be read — {err}. Nothing was written; "
            "run `lanes route` again to rebuild it.",
            file=sys.stderr,
        )
        return 2


def _still_red(after, named: bool) -> str:
    """What is still red after a refresh, said once. ``named`` is true when each failed page has
    already been printed with its command, so only the verdict the figures add is left to say."""
    stale = [r for r in after.reports if not r.ok]
    lines = [] if named else [r.explain() for r in stale]
    if any(r.figures_old for r in stale):
        lines.append(
            "the sending figures are old — refresh them with the email-sequence skill, "
            "then run --refresh-all again"
        )
    return "\n".join([*lines, after.summary] if lines else [])


def _run(args: argparse.Namespace, root: Path | None) -> int:

    if args.refresh_all:
        done = refresh_pages(args.profile, root, stubs=not args.no_stubs)
        for path in done.written:
            print("wrote " + str(path))
        for page, why in done.retired:
            print(f"{page}: retired candidate — {why}; not re-rendered")
        # A refresh that half-worked and reported success is how the scoped pages went stale in
        # the first place: each failure is named, and the exit code says the routine is not done.
        for page, why in done.failures:
            print(f"FAILED to re-render {page}: {why}", file=sys.stderr)
        # The exit code is the CHECK's verdict after the refresh, not "no page raised": a re-render
        # cannot refresh the sending figures, so a refresh that exits 0 over old figures would be
        # a green routine over a red check. Stated once, with the one remedy.
        after = check_all_pages(args.profile, root)
        if not after.ok and (text := _still_red(after, bool(done.failures))):
            print(text, file=sys.stderr)
        return 0 if after.ok and not done.failures else 1

    if args.check_fresh:
        if args.scope is None and args.campaign is None:
            # No scope named = "is anything stale?", which is the question `status`, `prospect`
            # and `email-sequence` all ask. Naming a scope asks about that one page, with the
            # same figures-age and page-file verdicts (`freshness.check_page`).
            every = check_all_pages(args.profile, root)
            print(every.explain(), file=sys.stdout if every.ok else sys.stderr)
            return 0 if every.ok else 1
        rep = check_fresh(args.profile, root, campaign=args.campaign, scope=args.scope)
        print(rep.explain(), file=sys.stdout if rep.ok else sys.stderr)
        if note := code_note(rep, code_fingerprint()):
            print(f"  note: {note}")
        return 0 if rep.ok else 1

    print(
        "wrote "
        + str(
            render_dashboard(
                args.profile,
                root,
                stubs=not args.no_stubs,
                campaign=args.campaign,
                scope=args.scope,
            )
        )
    )
    return 0
