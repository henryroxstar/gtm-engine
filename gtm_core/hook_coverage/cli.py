from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from ..paths import resolve_knowledge_file, resolve_profiles_root
from .audit import audit_campaign
from .backlog import BacklogUnreadable, backlog, render_backlog
from .config import MIN_ARGUMENTS, MIN_RECIPIENTS, MIN_SEGMENT_FIT, MIN_SIGNAL_ATTESTATION
from .matrix import parse_matrix
from .render import render, unresolved_json


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="gtm_core.hook_coverage",
        description=(
            "Measure a campaign's message axis: which matrix cells its specs declare, "
            "whether its arguments are distinguishable, and which personas hold "
            "recipients no spec addresses."
        ),
    )
    p.add_argument("--profile", required=True, help="active profile (tenant)")
    p.add_argument("--campaign", default="", help="campaign slug from cells.toml (default: all)")
    p.add_argument(
        "--overlay",
        default=None,
        help="resolve hook-matrix.md through this experiment overlay "
        "(profiles/<t>/experiments/<slug>/) instead of the live one. Explicit and never "
        "ambient, exactly like --product: an experiment that could be bound from the "
        "environment would outlive the run that asked for it. Note it reaches the MATRIX "
        "only — this command never reads premise-vocab.toml, so an overlay that swaps one "
        "changes nothing here.",
    )
    p.add_argument(
        "--backlog",
        action="store_true",
        help="list every matrix cell no spec declares, with each cell's first sentence, "
        "then stop. A report, never a refusal — and deliberately NOT gated on recipient "
        "volume: that gate is what hid an unused cell for three weeks",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="machine-readable output. With --backlog: the full unused-cell list. Otherwise: "
        "the FULL unresolved-title counter, which the rendered text caps at three exemplars "
        "— three out of a long tail is a sample, and harvesting synonyms from a sample is "
        "how the loop goes back to being recalled instead of evidence-driven",
    )
    p.add_argument(
        "--matrix-only",
        action="store_true",
        help="parse and summarise the profile's hook-matrix.md, then stop",
    )
    p.add_argument(
        "--min-recipients",
        type=int,
        default=MIN_RECIPIENTS,
        help=f"recipients a persona needs before 'unaddressed' is a finding "
        f"(default {MIN_RECIPIENTS})",
    )
    p.add_argument(
        "--min-arguments",
        type=int,
        default=MIN_ARGUMENTS,
        help=f"distinct arguments a campaign should carry (default {MIN_ARGUMENTS})",
    )
    p.add_argument(
        "--min-segment-fit",
        type=float,
        default=MIN_SEGMENT_FIT,
        help=f"share of a spec's recipients that must sit in its declared cell's "
        f"segment (default {MIN_SEGMENT_FIT:.2f})",
    )
    p.add_argument(
        "--min-signal-attestation",
        type=float,
        default=MIN_SIGNAL_ATTESTATION,
        help=f"share of recipients whose evidence must attest the declared signal; "
        f"advisory only (default {MIN_SIGNAL_ATTESTATION:.2f})",
    )
    p.add_argument(
        "--include-drafts",
        action="store_true",
        help="also audit drafted cells under prospects/evals/drafts/ (absent from "
        "cells.toml by design, so invisible without this)",
    )
    p.add_argument(
        "--include-packs",
        action="store_true",
        help="also audit the campaign's 1:1 Tier-A outreach packs under accounts/ "
        "(found by the campaign slug's trailing date; they are not in cells.toml, so "
        "argument-monotone is blind to them without this)",
    )
    p.add_argument("--warn-only", action="store_true", help="report findings but exit 0")
    args = p.parse_args(argv)

    if args.backlog:
        if args.min_recipients != MIN_RECIPIENTS:
            # Refuse rather than ignore. A run that passed --min-recipients and got a report
            # computed without it would reasonably believe the threshold had been applied,
            # and the whole point of this mode is that no threshold is.
            print(
                "--backlog takes no recipient threshold: a cell is unused or it is not, and "
                "a volume gate here is what hid one for three weeks. Drop --min-recipients.",
                file=sys.stderr,
            )
            return 2
        try:
            b = backlog(args.profile, include_drafts=args.include_drafts, overlay=args.overlay)
        except BacklogUnreadable as exc:
            # Named error, never a partial report: a backlog computed from a half-read
            # cells.toml lists live cells as unused, and a drafter acts on it.
            print(f"backlog could not be computed — {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(
                json.dumps(
                    {
                        "profile": args.profile,
                        "matrix": str(b.matrix_path),
                        "cells_total": b.cells_total,
                        "used": b.used,
                        "specs_read": b.specs_read,
                        "include_drafts": b.include_drafts,
                        "unused": [asdict(c) for c in b.unused],
                        "unmapped": [asdict(c) for c in b.unmapped],
                    },
                    indent=1,
                )
            )
        else:
            print(render_backlog(b))
        return 0

    if args.matrix_only:
        matrix = parse_matrix(
            resolve_knowledge_file(
                resolve_profiles_root(), args.profile, "hook-matrix.md", overlay=args.overlay
            ),
            profile=args.profile,
        )
        if not matrix.ok:
            print(f"{args.profile:<16} UNSUPPORTED ({matrix.shape}) — {matrix.reason}")
            return 0 if args.warn_only else 1
        print(
            f"{args.profile:<16} {matrix.shape:<10} {len(matrix.cells):>4} cell(s) · "
            f"{len(matrix.personas)} {matrix.row_axis}(s) x {len(matrix.signals)} signal(s) · "
            f"segments: {', '.join(matrix.segments)}"
        )
        for label in matrix.unmapped_personas():
            print(f"  unmapped {matrix.row_axis}: {label}")
        return 0

    cov = audit_campaign(
        args.profile,
        args.campaign,
        min_recipients=args.min_recipients,
        min_arguments=args.min_arguments,
        min_segment_fit=args.min_segment_fit,
        min_signal_attestation=args.min_signal_attestation,
        include_drafts=args.include_drafts,
        include_packs=args.include_packs,
        overlay=args.overlay,
    )
    if args.json:
        print(json.dumps(unresolved_json(cov), indent=1, ensure_ascii=False))
    else:
        print(render(cov))
    return 0 if (args.warn_only or not cov.failed) else 1
