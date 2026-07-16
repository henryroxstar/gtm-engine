"""CLI over :class:`gtm_core.ledgers.Ledgers` — the skills' write path to the ledgers.

Skills are markdown executed by the brain via tools; they cannot import Python.
So instead of having the LLM hand-write JSONL (wrong ts, wrong paths, tenant
bleed), the content-* skills shell out to this CLI, which delegates to the real
``Ledgers`` (correct ``ts`` stamping, ``content/<profile>/`` layout, tenant
isolation). Stdlib-only and SDK-free so it imports cleanly with no server.

VPS invocation:   python -m gtm_core.ledger_cli <subcommand> ...
Local invocation: python "$CLAUDE_PLUGIN_ROOT/lib/gtm_core/ledger_cli.py" <subcommand> ...

Subcommands::

    python -m gtm_core.ledger_cli append-history     --profile P --json '{...}'
    python -m gtm_core.ledger_cli append-cost        --profile P --json '{...}'
    python -m gtm_core.ledger_cli write-run-manifest --profile P --json '{...}'
    python -m gtm_core.ledger_cli month-total        --profile P [--month YYYY-MM] [--cap USD]
    python -m gtm_core.ledger_cli record-manual-publish --profile P --asset PATH --item-id ID [--url U] [--source S] [--platform linkedin]

``month-total`` prints a JSON line ``{"profile","month","total_usd","cap_usd","over_cap"}``;
with ``--cap`` it also exits 2 when the month total is at/over the cap (a scriptable
hard stop before a metered call).

``record-manual-publish`` records a ``published`` event for a post the operator put up
**by hand** (no cockpit / Hermes call). It reads the asset's ``body`` (the exact bytes
that go live — see content-publish Step 1), computes the SAME ``content_sha256`` the
automated publisher uses (:func:`gtm_core.publish_hash.content_hash`), and appends the
event. Stamping that hash is what makes a manual post visible to the durable idempotency
ledger (:meth:`gtm_core.ledgers.Ledgers.published_content_hashes`), so later enabling
automated publish cannot re-send the same bytes. Prints the recorded ``content_sha256``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ledgers import Ledgers
from .paths import PathConfig
from .publish_hash import content_hash


def _load_json(raw: str) -> dict:
    """Parse the --json payload into a dict (clear error on bad input)."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[ledger] --json is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise SystemExit("[ledger] --json must be a JSON object")
    return obj


def _ledgers(args) -> Ledgers:
    cfg = PathConfig.from_env(repo_root=args.repo_root)
    return Ledgers(cfg, args.profile)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gtm_core.ledger_cli",
        description="Write/read the per-profile content ledgers (history / costs / runs).",
    )
    parser.add_argument("--repo-root", type=Path, default=None, help="Override repo root.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("append-history", "append-cost", "write-run-manifest"):
        p = sub.add_parser(name)
        p.add_argument("--profile", required=True)
        p.add_argument("--json", required=True, dest="payload", help="JSON object to record.")

    mt = sub.add_parser("month-total")
    mt.add_argument("--profile", required=True)
    mt.add_argument("--month", default=None, help="YYYY-MM window (default: current UTC month).")
    mt.add_argument("--cap", type=float, default=None, help="If set, exit 2 when total >= cap.")

    rmp = sub.add_parser("record-manual-publish")
    rmp.add_argument("--profile", required=True)
    rmp.add_argument("--asset", required=True, type=Path, help="Path to the LinkedIn asset.json.")
    rmp.add_argument("--item-id", required=True, dest="item_id")
    rmp.add_argument("--url", default=None, help="Live post URL, if known.")
    rmp.add_argument("--source", default=None, help="Content source, e.g. 'journey' or 'news'.")
    rmp.add_argument("--platform", default="linkedin")

    args = parser.parse_args(argv)
    led = _ledgers(args)

    if args.cmd == "append-history":
        led.append_history(_load_json(args.payload))
        print("ok")
        return 0

    if args.cmd == "append-cost":
        led.append_cost(_load_json(args.payload))
        print("ok")
        return 0

    if args.cmd == "write-run-manifest":
        path = led.write_run_manifest(_load_json(args.payload))
        print(str(path))
        return 0

    if args.cmd == "record-manual-publish":
        try:
            asset = json.loads(args.asset.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SystemExit(f"[ledger] asset not found: {args.asset}") from exc
        except json.JSONDecodeError as exc:
            raise SystemExit(f"[ledger] asset is not valid JSON: {exc}") from exc
        body = asset.get("body")
        if not isinstance(body, str) or not body.strip():
            raise SystemExit(f"[ledger] asset {args.asset} has no non-empty 'body' to publish")
        # Same bytes content-publish would send (the asset body, verbatim, no media),
        # hashed with the same key the automated publisher uses → durable idempotency.
        digest = content_hash(body, ())
        record = {
            "event": "published",
            "platform": args.platform,
            "item_id": args.item_id,
            "content_sha256": digest,
            "manual": True,
        }
        if args.source:
            record["source"] = args.source
        if args.url:
            record["url"] = args.url
        led.append_history(record)
        print(digest)
        return 0

    if args.cmd == "month-total":
        total = led.month_cost_total(args.month)
        over = args.cap is not None and total >= args.cap
        print(
            json.dumps(
                {
                    "profile": args.profile,
                    "month": args.month or "current",
                    "total_usd": round(total, 4),
                    "cap_usd": args.cap,
                    "over_cap": over,
                }
            )
        )
        return 2 if over else 0

    parser.error(f"unknown command {args.cmd!r}")
    return 1  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
