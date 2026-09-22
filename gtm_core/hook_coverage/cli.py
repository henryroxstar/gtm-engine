from __future__ import annotations

import argparse

from ..paths import resolve_knowledge_file, resolve_profiles_root
from .audit import audit_campaign
from .config import MIN_ARGUMENTS, MIN_RECIPIENTS, MIN_SEGMENT_FIT, MIN_SIGNAL_ATTESTATION
from .matrix import parse_matrix
from .render import render


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

    if args.matrix_only:
        matrix = parse_matrix(
            resolve_knowledge_file(resolve_profiles_root(), args.profile, "hook-matrix.md"),
            profile=args.profile,
        )
        if not matrix.ok:
            print(f"{args.profile:<16} UNSUPPORTED ({matrix.shape}) — {matrix.reason}")
            return 0 if args.warn_only else 1
        print(
            f"{args.profile:<16} {matrix.shape:<10} {len(matrix.cells):>4} cell(s) · "
            f"{len(matrix.personas)} persona(s) x {len(matrix.signals)} signal(s) · "
            f"segments: {', '.join(matrix.segments)}"
        )
        for label in matrix.unmapped_personas():
            print(f"  unmapped persona: {label}")
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
    )
    print(render(cov))
    return 0 if (args.warn_only or not cov.failed) else 1
