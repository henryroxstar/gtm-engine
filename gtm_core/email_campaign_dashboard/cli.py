from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
        "--check-fresh",
        action="store_true",
        help=(
            "do not render — report whether the page for this scope is still built from "
            "what is on disk now, and exit non-zero if not. A stale page renders "
            "identically to a current one, so rendering proves nothing about the one "
            "already sitting there."
        ),
    )
    args = ap.parse_args(argv)
    root = Path(args.content_root) if args.content_root else None

    if args.check_fresh:
        rep = check_fresh(args.profile, root, campaign=args.campaign, scope=args.scope)
        print(rep.explain(), file=sys.stdout if rep.ok else sys.stderr)
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
