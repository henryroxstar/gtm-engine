from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .consolidate import consolidate
from .dossier import accounts_needing_dossier, tier_a_needing_dossier
from .queues import next_verification_batch, pool_status, split_by_signal


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.prospects_consolidate")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("consolidate", help="sweep raw exports into the master list")
    c.add_argument("--profile", required=True)
    c.add_argument(
        "--dnc-file", help="path to a JSON list of DNC emails (default: .cache/dnc-emails.json)"
    )
    c.add_argument("--allow-shrink", action="store_true")
    c.add_argument(
        "--require-dnc",
        action="store_true",
        help="fail hard if the DNC cache is missing/empty/undatable/stale instead of "
        "consolidating with no suppression (use for every unattended run)",
    )
    c.add_argument(
        "--market",
        action="append",
        help="override the profile's target_markets (repeatable); rows outside it never "
        "reach ready-to-load.csv",
    )
    c.add_argument(
        "--strict-market",
        action="store_true",
        help="also drop rows with no country — unresolved is not the same as allowed",
    )
    c.add_argument(
        "--reclassify",
        action="store_true",
        help="re-run classify_confidence() against the newer source row for an already-"
        "present email, upgrading conf_tier when the fresh export carries a better "
        "verification signal (e.g. a late-added Email Status column)",
    )
    c.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="with --reclassify, also apply a WEAKER conf_tier from the newer source row "
        "(default: upgrades only)",
    )
    c.add_argument(
        "--rebuild-master",
        action="store_true",
        help="recompute conf_tier for every row already in master-list.csv from its own "
        "stored email_status/conf fields — no new source export needed",
    )
    c.add_argument(
        "--unattended",
        action="store_true",
        help="unattended mode: bypass warning prompts and default to safe actions",
    )

    s = sub.add_parser("status", help="read-only pool status, no re-sweep")
    s.add_argument("--profile", required=True)

    v = sub.add_parser("verify-batch", help="emit next N hold-queue rows as Saleshandy import JSON")
    v.add_argument("--profile", required=True)
    v.add_argument("--limit", type=int, default=50)
    v.add_argument("--dnc-file", help="path to the DNC cache (default: .cache/dnc-emails.json)")
    v.add_argument(
        "--require-dnc",
        action="store_true",
        help="fail hard rather than emitting a batch checked against no suppression list",
    )

    sp = sub.add_parser(
        "split-by-signal",
        help="split ready-to-load.csv into a signal-led list and a generic one",
    )
    sp.add_argument("--profile", required=True)

    ta = sub.add_parser(
        "tier-a-needing-dossier",
        help="Tier-A accounts with no dossier yet -> candidates for the prospecting-brief sweep",
    )
    ta.add_argument("--profile", required=True)

    an = sub.add_parser(
        "accounts-needing-dossier",
        help="accounts with no dossier yet, any tier by default -> candidates for the "
        "dossier sweep before a bulk sequence load",
    )
    an.add_argument("--profile", required=True)
    an.add_argument(
        "--tier",
        default="",
        help="restrict to one tier (e.g. A); default is every tier",
    )

    args = ap.parse_args(argv)
    if args.cmd == "consolidate":
        result = consolidate(
            args.profile,
            dnc_file=Path(args.dnc_file) if args.dnc_file else None,
            allow_shrink=args.allow_shrink,
            require_dnc=args.require_dnc,
            target_markets=args.market,
            strict_market=args.strict_market,
            reclassify=args.reclassify,
            allow_downgrade=args.allow_downgrade,
            rebuild_master=args.rebuild_master,
            unattended=args.unattended,
        )
    elif args.cmd == "verify-batch":
        result = next_verification_batch(
            args.profile,
            limit=args.limit,
            dnc_file=Path(args.dnc_file) if args.dnc_file else None,
            require_dnc=args.require_dnc,
        )
    elif args.cmd == "split-by-signal":
        result = split_by_signal(args.profile)
    elif args.cmd == "tier-a-needing-dossier":
        result = tier_a_needing_dossier(args.profile)
    elif args.cmd == "accounts-needing-dossier":
        result = accounts_needing_dossier(args.profile, tier=args.tier or None)
    else:
        result = pool_status(args.profile)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0
