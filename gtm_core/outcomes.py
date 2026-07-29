"""The outcomes ledger — the capture tier of the closed learning loop (PRD Phase 4).

Records GTM *results* (email replies, meetings booked, publish engagement) as an append-only
``content/<profile>/outcomes.jsonl``, alongside the existing activity/cost ledgers. Nothing captures
outcomes today, so this is the sink: a Saleshandy ``get_outcomes`` consumer, the publish path, or a
manual entry all append here through :func:`append_outcome` / the CLI. The distiller
(``gtm_core.gtm_distill``) reads it back and turns it into learnings.

Files-only, no DB — same shape and conventions as ``gtm_core.ledgers`` (per-profile JSONL under
``content/<profile>/``, an auto-stamped ``ts``, robust line-skipping reads). Reusable across every
profile: takes ``content_root`` + ``profile``, never a global.

An outcome record (all fields optional except ``channel`` + ``outcome``)::

    {
      "ts":      "2026-07-18T12:00:00Z",   # auto-stamped if absent
      "channel": "email" | "linkedin" | "publish" | ...,
      "outcome": "sent" | "reply" | "meeting" | "open" | "click" | "engagement" | ...,
      "ref":     "<sequence/step/post id>",   # what produced it, for attribution
      "account": "<account-slug>",            # optional (PII — stays under content/)
      "tags":    ["myth-bust", "regtech", "ciso"],  # angle/hook/persona/segment — the learning axis
      "value":   1,                            # count this row contributes (aggregate rows use >1)
      "meta":    { ... }
    }
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from .paths import resolve_content_root

#: Outcome buckets used to derive rates. Everything else is still counted, just not rate-bearing.
ATTEMPT_OUTCOMES = frozenset({"sent", "send", "delivered", "enrolled", "contacted"})
REPLY_OUTCOMES = frozenset({"reply", "replied", "positive_reply", "response"})
MEETING_OUTCOMES = frozenset({"meeting", "meeting_booked", "demo", "opportunity"})


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def outcomes_path(content_root: Path, profile: str) -> Path:
    return content_root / profile / "outcomes.jsonl"


def append_outcome(
    content_root: Path, profile: str, record: dict, *, now: str | None = None
) -> None:
    """Append one outcome record (timestamped if absent) to ``content/<profile>/outcomes.jsonl``."""
    path = outcomes_path(content_root, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    enriched = dict(record)
    enriched.setdefault("ts", now or _utc_now_iso())
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(enriched, ensure_ascii=False) + "\n")


def read_outcomes(
    content_root: Path, profile: str, *, since_month: str | None = None
) -> list[dict]:
    """All outcome rows for ``profile`` (optionally filtered to a ``YYYY-MM`` window by ``ts``).
    Malformed lines are skipped, matching the ledger reader's robustness."""
    path = outcomes_path(content_root, profile)
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_month:
                ts = record.get("ts", "")
                if not isinstance(ts, str) or not ts.startswith(since_month):
                    continue
            rows.append(record)
    return rows


def _blank_bucket() -> dict:
    return {"counts": {}, "sent": 0, "replies": 0, "meetings": 0}


def _add(bucket: dict, outcome: str, value: float) -> None:
    bucket["counts"][outcome] = bucket["counts"].get(outcome, 0) + value
    if outcome in ATTEMPT_OUTCOMES:
        bucket["sent"] += value
    if outcome in REPLY_OUTCOMES:
        bucket["replies"] += value
    if outcome in MEETING_OUTCOMES:
        bucket["meetings"] += value


def _finalize(bucket: dict) -> dict:
    sent = bucket["sent"]
    bucket["reply_rate"] = round(bucket["replies"] / sent, 4) if sent else None
    bucket["meeting_rate"] = round(bucket["meetings"] / sent, 4) if sent else None
    return bucket


def summarize(rows: list[dict]) -> dict:
    """Aggregate outcome rows by channel and by tag, with reply/meeting rates where a ``sent``
    denominator exists. ``by_tag`` is the learning axis — it answers "which angle/persona wins?"."""
    by_channel: dict[str, dict] = {}
    by_tag: dict[str, dict] = {}
    totals = _blank_bucket()

    for r in rows:
        outcome = str(r.get("outcome", "")).strip().lower()
        if not outcome:
            continue
        try:
            value = float(r.get("value", 1) or 0)
        except (TypeError, ValueError):
            value = 1.0
        channel = str(r.get("channel", "unknown")).strip().lower() or "unknown"

        _add(totals, outcome, value)
        _add(by_channel.setdefault(channel, _blank_bucket()), outcome, value)
        tags = r.get("tags") or []
        if isinstance(tags, list):
            for tag in tags:
                _add(by_tag.setdefault(str(tag), _blank_bucket()), outcome, value)

    return {
        "totals": _finalize(totals),
        "by_channel": {k: _finalize(v) for k, v in sorted(by_channel.items())},
        "by_tag": {k: _finalize(v) for k, v in sorted(by_tag.items())},
    }


# --- CLI ----------------------------------------------------------------------


def _all_profiles(profiles_root: Path) -> list[str]:
    if not profiles_root.is_dir():
        return []
    return sorted(
        c.name for c in profiles_root.iterdir() if c.is_dir() and (c / "PROFILE.md").is_file()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.outcomes",
        description="Append/summarize the GTM outcomes ledger (the closed-loop capture tier).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    ap = sub.add_parser("append", help="append one outcome record")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--outcome", required=True)
    ap.add_argument("--ref", default=None)
    ap.add_argument("--account", default=None)
    ap.add_argument("--tag", action="append", default=[], help="repeatable (angle/persona/segment)")
    ap.add_argument("--value", type=float, default=1.0)
    ap.add_argument("--content-root", default=None)

    sp = sub.add_parser("summary", help="aggregate outcomes by channel + tag")
    sp.add_argument("--profile", default=None)
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--month", default=None, help="filter to a YYYY-MM window")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--content-root", default=None)

    args = parser.parse_args(argv)
    content_root = (
        Path(args.content_root).expanduser().resolve()
        if args.content_root
        else resolve_content_root()
    )

    if args.cmd == "append":
        record = {
            "channel": args.channel,
            "outcome": args.outcome,
            "value": args.value,
            "tags": args.tag,
        }
        if args.ref:
            record["ref"] = args.ref
        if args.account:
            record["account"] = args.account
        append_outcome(content_root, args.profile, record)
        print(f"appended outcome to {outcomes_path(content_root, args.profile)}")
        return 0

    # summary
    if args.all:
        from .paths import resolve_profiles_root

        profiles = _all_profiles(resolve_profiles_root())
    elif args.profile:
        profiles = [args.profile]
    else:
        raise SystemExit("[outcomes] pass --profile <slug> or --all")

    payload = {
        p: summarize(read_outcomes(content_root, p, since_month=args.month)) for p in profiles
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    for profile, s in payload.items():
        t = s["totals"]
        print(
            f"\nprofile: {profile} — reply_rate={t['reply_rate']} meeting_rate={t['meeting_rate']}"
        )
        for tag, b in s["by_tag"].items():
            print(
                f"  tag {tag:<18} sent={b['sent']:g} replies={b['replies']:g} rate={b['reply_rate']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
