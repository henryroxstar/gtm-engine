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
    python -m gtm_core.ledger_cli month-units        --profile P --tool T --unit U [--month YYYY-MM] [--cap N]
    python -m gtm_core.ledger_cli denials            --profile P [--days 7]
    python -m gtm_core.ledger_cli reconcile-renders --profile P [--month YYYY-MM]
    python -m gtm_core.ledger_cli record-manual-publish --profile P --asset PATH --item-id ID [--url U] [--source S] [--platform linkedin] [--media URL ...] [--ref REF]
    python -m gtm_core.ledger_cli record-schedule-void --profile P (--content-sha256 HASH | --post TEXT [--media URL ...]) [--note N]

``denials`` prints a JSON summary of the permission-event ledger (``denials.jsonl``,
written by ``agent.denial_log``) over the trailing window — totals plus by-tool /
by-decision / by-outcome counts and the newest records. This is the review surface for
"what got blocked this week".

``month-total`` prints a JSON line ``{"profile","month","total_usd","cap_usd","over_cap"}``;
with ``--cap`` it also exits 2 when the month total is at/over the cap (a scriptable
hard stop before a metered call).

``month-units`` is the same idiom for a flat-subscription/credit-pool connector whose real
limit is a unit COUNT, not a dollar figure — e.g. Reap's ``media_credits``/``ai_credits`` pools
(no per-call cost, no balance-readback tool at all). Prints
``{"profile","tool","unit","month","total","cap","over_cap"}`` via
:meth:`gtm_core.ledgers.Ledgers.month_unit_total`; with ``--cap`` it exits 2 at/over cap, same
hard-stop idiom as ``month-total``. The cap value itself lives in the profile's ``PROFILE.md``
"Connector plans & entitlements" block (prose, read by the calling skill) — this command only
enforces whatever cap it is given, and always echoes it back so a stale/typo'd cap is visible in
the printed line rather than silently trusted.

``record-manual-publish`` records a ``published`` event for a post the operator put up
**by hand** (no cockpit / Hermes call). It reads the asset's ``body`` (the exact bytes
that go live — see content-publish Step 1), computes the SAME ``content_sha256`` the
automated publisher uses (:func:`gtm_core.publish_hash.content_hash`) — over the body
**and** any ``--media`` urls, matching exactly what :meth:`agent.publish.Publisher.publish`
hashes — and appends the event. Stamping that hash is what makes a manual post visible to
the durable idempotency ledger (:meth:`gtm_core.ledgers.Ledgers.published_content_hashes`),
so later enabling automated publish cannot re-send the same bytes. Omitting ``--media`` for
a post that shipped with media would record the wrong hash (silently missing the automated
path's dedupe), so pass every media url, in the same order the post used. ``--ref`` records
the provider's post id/permalink for cross-referencing against
``content/<profile>/outcomes.jsonl`` (:mod:`gtm_core.outcomes`) — a separate join key from
``item_id``, which only identifies the local content item. Prints the recorded
``content_sha256``.

``record-schedule-void`` is the operator's escape hatch for a scheduled post they
cancelled in the scheduler's own UI (nothing in this system reconciles a fired-or-not
schedule automatically). It appends a
``schedule_voided`` event that ``published_content_hashes`` honours ONLY when the
hash's most recent committed event was ``scheduled`` — it can never unblock a hash
that already went out ``published`` (that would let live bytes be re-approved and
sent a second time). Identify the hash either directly (``--content-sha256``, e.g.
copied from the cockpit's staged preview) or by re-deriving it from the exact approved
bytes (``--post`` [+ repeated ``--media``] — the SAME content_hash the publisher
used, media order matters). Prints the voided ``content_sha256``.
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


def reconcile_renders(
    profile: str, *, month: str | None = None, repo_root: Path | None = None
) -> dict:
    """Answer "what did video actually cost" from render manifests, not from trust.

    W0.3. In August 2026 the video programme spent ~580 credits that ``costs.jsonl`` records as $0
    — generations drew a pre-purchased credit pool, so nothing forced a metering write and the
    profile's monthly cap never saw it. ``render_manifest`` now refuses an unmetered synthetic
    manifest, but that only binds renders written *after* the gate. This verb reads what is already
    on disk and reports the gap, so the historical spend is recoverable instead of archaeological.

    Returns per-manifest rows plus totals: credits claimed by manifests, how many cite a
    ``costs.jsonl`` row, and which do not (``unmetered``) — the actionable list.
    """
    root = repo_root if repo_root is not None else Path.cwd()
    video_dir = root / "content" / profile / "video"
    rows: list[dict] = []
    for manifest_path in sorted(video_dir.glob("**/render-*.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rows.append({"path": str(manifest_path.relative_to(root)), "error": str(exc)})
            continue
        ts = str(data.get("cost_ledger_ts", "") or "")
        if month and not ts.startswith(month):
            # A manifest with no ts cannot be excluded by a month filter — that is exactly the
            # population this verb exists to surface, so keep it and let the caller see it.
            if ts:
                continue
        rows.append(
            {
                "path": str(manifest_path.relative_to(root)),
                "slug": data.get("slug", ""),
                "ratio": data.get("ratio", ""),
                "cost_credits": float(data.get("cost_credits", 0) or 0),
                "cost_source": data.get("cost_source", "") or "",
                "cost_ledger_ts": ts,
                "metered": bool(ts) or data.get("cost_source") == "free",
            }
        )
    priced = [r for r in rows if "error" not in r]
    unmetered = [r for r in priced if not r["metered"]]
    return {
        "profile": profile,
        "month": month,
        "manifests": len(rows),
        "credits_claimed": round(sum(r["cost_credits"] for r in priced), 4),
        "credits_unmetered": round(sum(r["cost_credits"] for r in unmetered), 4),
        "unmetered_count": len(unmetered),
        "unmetered": [r["path"] for r in unmetered],
        "rows": rows,
    }


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

    mu = sub.add_parser("month-units")
    mu.add_argument("--profile", required=True)
    mu.add_argument("--tool", required=True, help="e.g. 'reap', 'rocketreach'.")
    mu.add_argument("--unit", required=True, help="e.g. 'media_credits', 'ai_credits', 'lookups'.")
    mu.add_argument("--month", default=None, help="YYYY-MM window (default: current UTC month).")
    mu.add_argument("--cap", type=float, default=None, help="If set, exit 2 when total >= cap.")

    dn = sub.add_parser("denials")
    dn.add_argument("--profile", required=True)
    dn.add_argument("--days", type=int, default=7, help="Trailing window in days (default 7).")

    rr = sub.add_parser("reconcile-renders")
    rr.add_argument("--profile", required=True)
    rr.add_argument("--month", default=None, help="YYYY-MM window (default: all time).")

    rmp = sub.add_parser("record-manual-publish")
    rmp.add_argument("--profile", required=True)
    rmp.add_argument("--asset", required=True, type=Path, help="Path to the LinkedIn asset.json.")
    rmp.add_argument("--item-id", required=True, dest="item_id")
    rmp.add_argument("--url", default=None, help="Live post URL, if known.")
    rmp.add_argument("--source", default=None, help="Content source, e.g. 'journey' or 'news'.")
    rmp.add_argument("--platform", default="linkedin")
    rmp.add_argument(
        "--media",
        action="append",
        default=[],
        help="Media URL the post shipped with (repeatable, order matters — feeds the same "
        "content_sha256 the automated publisher computes).",
    )
    rmp.add_argument(
        "--ref", default=None, help="Provider post id/permalink — join key for outcomes.jsonl."
    )

    rsv = sub.add_parser("record-schedule-void")
    rsv.add_argument("--profile", required=True)
    rsv.add_argument("--content-sha256", default=None, help="The hash to void directly.")
    rsv.add_argument("--post", default=None, help="Re-derive the hash from the approved post text.")
    rsv.add_argument(
        "--media", action="append", default=[], help="Media URL (repeatable, order matters)."
    )
    rsv.add_argument("--note", default=None, help="Why this schedule was voided.")

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

    if args.cmd == "denials":
        print(json.dumps(led.denials_summary(days=args.days), ensure_ascii=False))
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
        # Same bytes content-publish would send (the asset body + any media urls, verbatim),
        # hashed with the same key the automated publisher uses → durable idempotency.
        media = tuple(args.media)
        digest = content_hash(body, media)
        record = {
            "event": "published",
            "platform": args.platform,
            "item_id": args.item_id,
            "content_sha256": digest,
            "manual": True,
        }
        if media:
            record["media"] = list(media)
        if args.source:
            record["source"] = args.source
        if args.url:
            record["url"] = args.url
        if args.ref:
            record["ref"] = args.ref
        led.append_history(record)
        print(digest)
        return 0

    if args.cmd == "record-schedule-void":
        if args.content_sha256 and args.post:
            raise SystemExit("[ledger] pass --content-sha256 OR --post, not both")
        if args.content_sha256:
            digest = args.content_sha256
        elif args.post:
            digest = content_hash(args.post, tuple(args.media))
        else:
            raise SystemExit("[ledger] record-schedule-void needs --content-sha256 or --post")
        record = {"event": "schedule_voided", "content_sha256": digest}
        if args.note:
            record["note"] = args.note
        led.append_history(record)
        print(digest)
        return 0

    if args.cmd == "month-units":
        total = led.month_unit_total(args.tool, args.unit, args.month)
        over = args.cap is not None and total >= args.cap
        print(
            json.dumps(
                {
                    "profile": args.profile,
                    "tool": args.tool,
                    "unit": args.unit,
                    "month": args.month or "current",
                    "total": total,
                    "cap": args.cap,
                    "over_cap": over,
                }
            )
        )
        return 2 if over else 0

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

    if args.cmd == "reconcile-renders":
        print(
            json.dumps(
                reconcile_renders(args.profile, month=args.month, repo_root=args.repo_root),
                indent=2,
            )
        )
        return 0

    parser.error(f"unknown command {args.cmd!r}")
    return 1  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
