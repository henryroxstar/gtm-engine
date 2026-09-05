"""The outcomes ledger — the capture tier of the closed learning loop (PRD Phase 4).

Records GTM *results* (email replies, meetings booked, publish engagement) as an append-only
``content/<profile>/outcomes.jsonl``, alongside the existing activity/cost ledgers. Nothing captures
outcomes today, so this is the sink: a Saleshandy ``get_outcomes`` consumer, the publish path, or a
manual entry all append here through :func:`append_outcome` / the CLI. The distiller
(``gtm_core.gtm_distill``) reads it back and turns it into learnings.

:func:`attribute` / the CLI ``attribute`` subcommand is the video-finish half of the loop
(PRD 2026-08-15-video-quality-master.md §5 Phase B): it turns a ``finish-<ratio>.json``
(:mod:`gtm_core.render_manifest`) into one ``outcome:"published"`` row keyed on the finished
file's own sha256 (``asset_sha256`` — survives a rename, unlike a path), so a lint/score/cost
property recorded at finish time can later be correlated against real performance rows appended
by ``content-outcomes-sync``. :func:`unattributed` / the CLI ``unattributed`` subcommand lists
every finished asset with no matching row — the gap list a daily cadence prints so the loop
staying open is visible rather than silently assumed closed.

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
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .paths import _safe_segment, resolve_content_root

#: Outcome buckets used to derive rates. Everything else is still counted, just not rate-bearing.
ATTEMPT_OUTCOMES = frozenset({"sent", "send", "delivered", "enrolled", "contacted"})
REPLY_OUTCOMES = frozenset({"reply", "replied", "positive_reply", "response"})
MEETING_OUTCOMES = frozenset({"meeting", "meeting_booked", "demo", "opportunity"})
#: Never a reply. Written by the reply ingest from ``isUnsubscribed`` so the ledger, not only
#: history.jsonl, knows who said stop; ``summarize`` counts it, rates never include it.
OPTOUT_OUTCOMES = frozenset({"opt_out", "unsubscribe"})

# --- content-performance buckets --------------------------------------------------------------
# Published content has a different denominator than outreach: an outreach row is rate-bearing
# against ``sent``, a content row against ``impressions``. Adding these lets the SAME ledger and the
# SAME tag correlation in ``gtm_core.gtm_distill`` serve both, instead of standing up a parallel
# content ledger. Sales rows are untouched — a row only joins a bucket whose outcome name it uses.
#
# ⚠️ **Only ever append COUNTS, never a pre-computed rate.** ``summarize`` sums ``value`` across
# rows, so a row like ``{"outcome": "engagement_rate", "value": 3.2}`` would sum percentages into
# nonsense. Providers that hand back a ready-made rate (Buffer's ``engagementRate``, for one) should
# have it DROPPED at ingest — append the underlying impressions/reactions/comments counts and let
# ``_finalize`` derive the rate here, so one definition holds across every provider.

#: The content denominator. "How many times it was shown" — impressions, views, plays.
IMPRESSION_OUTCOMES = frozenset({"impression", "impressions", "view", "views", "play", "plays"})

#: Content engagement numerator. Deliberately broad: platforms name the same act differently, and a
#: name absent here is still counted in ``counts`` — just not rate-bearing.
CONTENT_ENGAGEMENT_OUTCOMES = frozenset(
    {
        "reaction",
        "reactions",
        "like",
        "likes",
        "comment",
        "comments",
        "repost",
        "reposts",
        "share",
        "shares",
        "retweet",
        "save",
        "saves",
        "bookmark",
        "bookmarks",
        "engagement",
        "engagements",
    }
)

#: Follow-through numerator — the action past the scroll.
CONTENT_CLICK_OUTCOMES = frozenset(
    {"click", "clicks", "link_click", "profile_visit", "follow", "follows"}
)

#: Names ``summarize``/``_finalize`` derive and write into a bucket's OWN output
#: (``reply_rate``, ``meeting_rate``, ``engagement_rate``, ``click_rate``). Appending a
#: row using one of these names — or anything ending ``_rate`` — collides with that
#: derived key and, because ``summarize`` sums ``value``, corrupts it into a nonsense
#: total (percentages summed as if they were counts). ``append_outcome`` refuses
#: these; the fix is always "append the underlying count instead."
RATE_OUTCOME_NAMES = frozenset({"reply_rate", "meeting_rate", "engagement_rate", "click_rate"})


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def outcomes_path(content_root: Path, profile: str) -> Path:
    # ``profile`` reaches here straight from --profile; guard it as a bare segment
    # before it is joined (CLAUDE.md tenant boundary).
    return content_root / _safe_segment(profile, "profile") / "outcomes.jsonl"


def append_outcome(
    content_root: Path, profile: str, record: dict, *, now: str | None = None
) -> None:
    """Append one outcome record (timestamped if absent) to ``content/<profile>/outcomes.jsonl``.

    Raises ``ValueError`` if ``record["outcome"]`` is a derived-rate name (see
    ``RATE_OUTCOME_NAMES``) — this is a footgun, not a valid outcome: the ledger
    derives rates itself, and a caller appending one would silently collide with
    that derived key under the same name.
    """
    outcome = record.get("outcome")
    if isinstance(outcome, str) and (outcome in RATE_OUTCOME_NAMES or outcome.endswith("_rate")):
        raise ValueError(
            f"outcome {outcome!r} is a derived rate — append the underlying counts "
            "(e.g. impressions/engagements/clicks, or sent/replies) and let "
            "summarize() derive the rate"
        )
    path = outcomes_path(content_root, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    enriched = dict(record)
    enriched.setdefault("ts", now or _utc_now_iso())
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(enriched, ensure_ascii=False) + "\n")


def read_outcomes(
    content_root: Path,
    profile: str,
    *,
    since_month: str | None = None,
    since: str | None = None,
) -> list[dict]:
    """All outcome rows for ``profile``, optionally windowed by ``ts``.

    ``since_month`` keeps a single ``YYYY-MM``. ``since`` keeps everything on or after a
    ``YYYY-MM-DD``, which is the finer window a mid-week read needs: on a Wednesday,
    month-to-date is three days early in a month and four weeks late in one, so it
    cannot answer "how is this week going". Both apply when both are given — neither
    silently wins.

    Malformed lines are skipped, matching the ledger reader's robustness.
    """
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
            ts = record.get("ts", "")
            if since_month:
                if not isinstance(ts, str) or not ts.startswith(since_month):
                    continue
            if since:
                # ISO-8601 timestamps sort lexicographically, so a prefix compare is a
                # date compare — and an unparseable ts is excluded rather than assumed
                # recent, which is the fail-closed direction for a freshness window.
                if not isinstance(ts, str) or ts[:10] < since:
                    continue
            rows.append(record)
    return rows


# --- video-finish attribution (PRD 2026-08-15 §5, A4/F5) ----------------------------------
# render_manifest.py writes what the finishing pipeline produced; nothing turned that into an
# outcomes row until now, so §7 criterion #3 ("every published asset has a ref row") and #1
# ("the would-post binary is recorded") stayed at zero no matter how many assets shipped.
# ``asset_sha256`` (the file's own content hash, computed here — not stored by the manifest
# writer) is the join key: it survives a rename, unlike a path.


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _resolve(raw: str, *, repo_root: Path) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else (repo_root / p)


def _predictor_band(score_path: Path) -> tuple[str | None, dict]:
    """Best-effort read of video-score's score.json ``recommended`` block. There is no writer
    or schema for this file (it is model-emitted) — treat it as optional and absent-tolerant
    rather than assuming any key exists beyond ``band``."""
    try:
        data = json.loads(score_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, {}
    recommended = data.get("recommended")
    if not isinstance(recommended, dict):
        return None, {}
    band = recommended.get("band")
    if not isinstance(band, str) or not band:
        return None, {}
    extra = {
        k: v
        for k, v in recommended.items()
        if k not in ("band", "band_note") and isinstance(v, (str, int, float, bool))
    }
    return band, extra


def attribute(
    content_root: Path,
    profile: str,
    *,
    finish_path: Path,
    render_path: Path | None = None,
    channel: str = "linkedin",
    ref: str | None = None,
    would_post: bool | None = None,
    tags: list[str] | tuple[str, ...] = (),
    score_path: Path | None = None,
    repo_root: Path | None = None,
    now: str | None = None,
) -> dict:
    """Append one ``outcome:"published"`` row from a finished video asset.

    Reads ``finish-<ratio>.json`` (:mod:`gtm_core.render_manifest`) for the finished path,
    plan id, and stage census; optionally ``render-<ratio>.json`` for ``identity_used``,
    provider, cost, and duration; optionally ``score.json`` for the predictor band. Refuses to
    append a second ``published`` row for the same ``asset_sha256`` — a re-run of the same
    finish must not silently double-count.
    """
    from .render_manifest import load_finish, load_render

    root = repo_root if repo_root is not None else Path.cwd()
    fm = load_finish(finish_path)
    meta: dict = {"plan_id": fm.plan_id, "census": dict(fm.census), "stages": list(fm.stages)}

    asset_sha256 = None
    if fm.executed:
        candidate = _resolve(fm.asset_path, repo_root=root)
        if candidate.is_file():
            asset_sha256 = _sha256_file(candidate)
            meta["asset_sha256"] = asset_sha256

    if render_path is not None:
        rm = load_render(render_path)
        meta["identity_used"] = list(rm.identity_used)
        meta["cost_credits"] = rm.cost_credits
        meta["duration_s"] = rm.duration_s
        if rm.provider:
            meta["provider"] = rm.provider

    if would_post is not None:
        meta["would_post"] = bool(would_post)

    tag_list = list(tags)
    if score_path is not None and score_path.is_file():
        band, extra = _predictor_band(score_path)
        if band:
            tag_list.append(f"predictor_band:{band}")
            if extra:
                meta["predictor"] = extra

    if asset_sha256 is not None:
        for row in read_outcomes(content_root, profile):
            if (
                row.get("outcome") == "published"
                and (row.get("meta") or {}).get("asset_sha256") == asset_sha256
            ):
                raise ValueError(
                    f"asset {asset_sha256[:12]}… is already attributed "
                    f"(ref={row.get('ref')!r}) — refusing a duplicate published row"
                )

    record: dict = {"channel": channel, "outcome": "published", "value": 1, "tags": tag_list}
    if ref:
        record["ref"] = ref
    record["meta"] = meta
    append_outcome(content_root, profile, record, now=now)
    return record


def unattributed(
    content_root: Path,
    profile: str,
    *,
    days: int | None = 14,
    repo_root: Path | None = None,
    now: str | None = None,
) -> list[dict]:
    """Every ``finish-*.json`` under ``content/<profile>/video/`` with no matching
    ``outcome:"published"`` row (joined on ``asset_sha256``) — the gap list for the daily
    cadence to print (B3). ``days=None`` disables the recency filter (used by tests; the CLI
    default of 14 keeps this from re-surfacing assets abandoned long ago)."""
    from .render_manifest import load_finish

    root = repo_root if repo_root is not None else Path.cwd()
    video_root = content_root / _safe_segment(profile, "profile") / "video"
    if not video_root.is_dir():
        return []

    cutoff_ts = None
    if days is not None:
        now_dt = datetime.strptime(now or _utc_now_iso(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        cutoff_ts = now_dt.timestamp() - days * 86400

    attributed = {
        (row.get("meta") or {}).get("asset_sha256")
        for row in read_outcomes(content_root, profile)
        if row.get("outcome") == "published" and (row.get("meta") or {}).get("asset_sha256")
    }

    gaps = []
    for finish_path in sorted(video_root.glob("*/finish-*.json")):
        if cutoff_ts is not None and finish_path.stat().st_mtime < cutoff_ts:
            continue
        try:
            fm = load_finish(finish_path)
        except (OSError, json.JSONDecodeError):
            # A file that vanished mid-glob or a truncated/corrupt write — genuinely unreadable,
            # not this profile's problem to surface as a gap. A TypeError (a manifest whose shape
            # doesn't match FinishManifest) is NOT caught here on purpose: that means the
            # producer wrote something render_manifest.load_finish() cannot parse, which is a
            # real bug that must surface loudly — silently skipping it here previously reported
            # "nothing unattributed" for an asset whose manifest was actually unreadable.
            continue
        if not fm.executed:
            continue
        candidate = _resolve(fm.asset_path, repo_root=root)
        if not candidate.is_file():
            continue
        asset_sha256 = _sha256_file(candidate)
        if asset_sha256 in attributed:
            continue
        gaps.append(
            {
                "slug": finish_path.parent.name,
                "ratio": fm.ratio,
                "finish_path": str(finish_path),
                "asset_sha256": asset_sha256,
            }
        )
    return gaps


def _blank_bucket() -> dict:
    return {
        "counts": {},
        "sent": 0,
        "replies": 0,
        "meetings": 0,
        # content-performance counters (§5.4)
        "impressions": 0,
        "content_engagements": 0,
        "clicks": 0,
    }


def _add(bucket: dict, outcome: str, value: float) -> None:
    bucket["counts"][outcome] = bucket["counts"].get(outcome, 0) + value
    if outcome in ATTEMPT_OUTCOMES:
        bucket["sent"] += value
    if outcome in REPLY_OUTCOMES:
        bucket["replies"] += value
    if outcome in MEETING_OUTCOMES:
        bucket["meetings"] += value
    if outcome in IMPRESSION_OUTCOMES:
        bucket["impressions"] += value
    if outcome in CONTENT_ENGAGEMENT_OUTCOMES:
        bucket["content_engagements"] += value
    if outcome in CONTENT_CLICK_OUTCOMES:
        bucket["clicks"] += value


def _finalize(bucket: dict) -> dict:
    sent = bucket["sent"]
    bucket["reply_rate"] = round(bucket["replies"] / sent, 4) if sent else None
    bucket["meeting_rate"] = round(bucket["meetings"] / sent, 4) if sent else None
    # Content rates share the shape but not the denominator: impressions, not sends. ``None`` when
    # there is no denominator, exactly like the outreach rates — a tag with engagements but no
    # impression row is genuinely unrateable, and must not silently read as 0%.
    impressions = bucket["impressions"]
    bucket["engagement_rate"] = (
        round(bucket["content_engagements"] / impressions, 4) if impressions else None
    )
    bucket["click_rate"] = round(bucket["clicks"] / impressions, 4) if impressions else None
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
    ap.add_argument(
        "--meta",
        default=None,
        help="optional JSON object of extra context, e.g. '{\"virality_index\": 62}' — never a "
        "substitute for --tag: meta is not queryable by the correlator, tags are",
    )
    ap.add_argument("--content-root", default=None)

    sp = sub.add_parser("summary", help="aggregate outcomes by channel + tag")
    sp.add_argument("--profile", default=None)
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--month", default=None, help="filter to a YYYY-MM window")
    sp.add_argument(
        "--since", default=None, metavar="YYYY-MM-DD", help="keep rows on or after this date"
    )
    sp.add_argument(
        "--days",
        type=int,
        default=None,
        help="keep the last N days (a week-to-date read; --since computed from --as-of)",
    )
    sp.add_argument(
        "--as-of",
        default=None,
        metavar="YYYY-MM-DD",
        help="pin today's date for --days (tests, replays)",
    )
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--content-root", default=None)

    at = sub.add_parser("attribute", help="record a finished video asset as published")
    at.add_argument("--profile", required=True)
    at.add_argument("--finish", required=True, type=Path, help="Path to finish-<ratio>.json")
    at.add_argument("--render", default=None, type=Path, help="Path to render-<ratio>.json")
    at.add_argument("--channel", default="linkedin")
    at.add_argument("--ref", default=None, help="Provider post id/permalink")
    at.add_argument(
        "--would-post",
        default=None,
        choices=("true", "false"),
        help="The would-post binary — was this asset actually fit to ship?",
    )
    at.add_argument("--tag", action="append", default=[], help="repeatable")
    at.add_argument("--score", default=None, type=Path, help="Path to score.json (optional)")
    at.add_argument("--content-root", default=None)
    at.add_argument("--repo-root", default=None, help="Base dir for relative manifest paths.")

    un = sub.add_parser("unattributed", help="list finished assets with no published row")
    un.add_argument("--profile", required=True)
    un.add_argument("--days", type=int, default=14, help="Recency window (default 14).")
    un.add_argument("--json", action="store_true")
    un.add_argument("--content-root", default=None)
    un.add_argument("--repo-root", default=None)

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
        if args.meta:
            try:
                meta = json.loads(args.meta)
            except json.JSONDecodeError as exc:
                print(f"[outcomes] --meta is not valid JSON: {exc}")
                return 2
            if not isinstance(meta, dict):
                print("[outcomes] --meta must be a JSON object")
                return 2
            record["meta"] = meta
        try:
            append_outcome(content_root, args.profile, record)
        except ValueError as exc:
            print(f"[outcomes] {exc}")
            return 2
        print(f"appended outcome to {outcomes_path(content_root, args.profile)}")
        return 0

    if args.cmd == "attribute":
        repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else None
        would_post = {"true": True, "false": False}.get(args.would_post)
        try:
            record = attribute(
                content_root,
                args.profile,
                finish_path=args.finish,
                render_path=args.render,
                channel=args.channel,
                ref=args.ref,
                would_post=would_post,
                tags=args.tag,
                score_path=args.score,
                repo_root=repo_root,
            )
        except ValueError as exc:
            print(f"[outcomes] {exc}")
            return 2
        print(json.dumps(record, ensure_ascii=False))
        return 0

    if args.cmd == "unattributed":
        repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else None
        gaps = unattributed(content_root, args.profile, days=args.days, repo_root=repo_root)
        if args.json:
            print(json.dumps(gaps, indent=2))
            return 0
        if not gaps:
            print(f"[outcomes] {args.profile}: nothing unattributed in the last {args.days}d")
            return 0
        for g in gaps:
            print(f"{g['slug']}  {g['ratio']}  {g['finish_path']}")
        return 0

    # summary
    if args.all:
        from .paths import resolve_profiles_root

        profiles = _all_profiles(resolve_profiles_root())
    elif args.profile:
        profiles = [args.profile]
    else:
        raise SystemExit("[outcomes] pass --profile <slug> or --all")

    since = args.since
    if args.days is not None:
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
        # Inclusive of today: `--days 7` on a Wednesday is Thu..Wed, the week an AE is
        # actually in the middle of, not the seven days ending yesterday.
        since = (as_of - timedelta(days=args.days - 1)).isoformat()
    payload = {
        p: summarize(read_outcomes(content_root, p, since_month=args.month, since=since))
        for p in profiles
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
