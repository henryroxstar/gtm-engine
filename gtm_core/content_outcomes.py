"""Platform analytics → the outcomes ledger: the producer the hook prior never had.

**The gap this closes.** ``gtm_core.hook_score``'s ``prior`` component and
``gtm_core.hooks.is_fatigued`` both read only rows whose ``outcome`` is in
:data:`gtm_core.outcomes.IMPRESSION_OUTCOMES`. Until this module, **nothing in the repo ever
appended one.** There was a producer for email replies (:mod:`gtm_core.sequencer_outcomes`),
one for email sends (:mod:`gtm_core.sequencer_sends`), and one for video publishes
(``gtm_core.outcomes attribute``, which writes an ``outcome:"published"`` row) — but none for
engagement. The consequences were structural, not cosmetic: every hook stayed ``status: test``
for ever, every ``hook_score`` returned ``prior_has_data: false`` / ``band: "uncertain"``, no
hook could ever fatigue, and the distiller's promote/demote candidates could never fire. The
cold-start override path in ``video-script`` / ``content-studio`` exists precisely because of
this — it is a workaround for a missing producer, and this is the producer.

**Why an operator export and not a fetch.** Automated collection is blocked: the Buffer MCP
server is unauthenticated, and there is no LinkedIn/IG/X analytics MCP. Every platform does
export its own post analytics as CSV. So the realistic path is the operator exporting (or
pasting) that file after a post has run, and a deterministic mapping turning it into rows —
not an agent eyeballing a dashboard and hand-composing ``outcomes append`` calls. A wrong tag
is worse than a missing one: ``gtm_distill._tag_value`` and ``hook_score._compute_prior`` join
on the ``key:`` prefix, so a mistyped tag does not read as unknown, it reads as **baseline**,
and the correlation silently disappears.

**The join, and why it needs no change to the publish gate.** An export row carries a post ref
and some counts; it does not know what a hook is. The attribution chain is entirely on disk::

    export ref ──▶ history.jsonl `published` row ──▶ item_id ──▶ plans/*.json item ──▶ hook_id

Two publish paths write that middle row and they carry different fields:

* the **manual** path (``ledger_cli record-manual-publish``, and ``content-studio`` Step 5)
  records ``item_id`` plus an optional ``url`` / ``ref`` — a direct join;
* the **automated** cockpit path (``agent/publish_dispatch``) records ``post_id`` and
  ``content_sha256`` but **no** ``item_id`` — so it is joined the way
  ``gtm_core.outcomes.attribute`` already joins a finished video: by recomputing the content
  digest of each asset (``gtm_core.publish_hash.content_hash``) and matching it. This is why
  this module needs no change to the approval-bound publish path.

A row that resolves to no item, or to an item with no ``hook_id``, is **refused and named** —
never written with a guessed tag, and never written as zero. Same discipline as
:mod:`gtm_core.sequencer_sends`: a missing field must not look like a measurement.

Counts only. A provider's ready-made ``engagementRate`` is DROPPED at ingest (``summarize``
sums ``value``, so appending a percentage produces nonsense); the ledger derives the rate from
the underlying counts, so one definition holds across every provider.

Untrusted input (§R5): an export is a file a platform wrote and an operator moved. Its text is
data to record, never an instruction to follow.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
from pathlib import Path

from gtm_core.outcomes import (
    CONTENT_CLICK_OUTCOMES,
    CONTENT_ENGAGEMENT_OUTCOMES,
    IMPRESSION_OUTCOMES,
    append_outcome,
    read_outcomes,
)
from gtm_core.paths import _safe_segment, resolve_content_root
from gtm_core.publish_hash import content_hash

SOURCE = "content_outcomes"

#: Column spellings platforms actually use → the canonical count name the ledger buckets on.
#: Deliberately explicit: a column NOT listed here is reported and skipped, never guessed into
#: a bucket. Adding a spelling is a one-line change with a test; guessing is a silent wrong number.
METRIC_ALIASES: dict[str, str] = {
    "impressions": "impressions",
    "impression": "impressions",
    "impression_count": "impressions",
    "views": "views",
    "view": "views",
    "video_views": "views",
    "plays": "plays",
    "reactions": "reactions",
    "likes": "reactions",
    "like": "reactions",
    "comments": "comments",
    "comment": "comments",
    "replies": "comments",
    "reposts": "reposts",
    "shares": "reposts",
    "share": "reposts",
    "retweets": "reposts",
    "saves": "saves",
    "bookmarks": "saves",
    "clicks": "clicks",
    "click": "clicks",
    "link_clicks": "clicks",
    "profile_visits": "profile_visit",
    "follows": "follows",
    "engagements": "engagements",
}

#: Column spellings that are a RATE, not a count. Dropped loudly rather than appended.
RATE_COLUMNS = frozenset(
    {
        "engagement_rate",
        "engagementrate",
        "engagement rate",
        "click_rate",
        "clickrate",
        "click through rate",
        "click_through_rate",
        "ctr",
        "rate",
    }
)

#: Column spellings that identify the post. First one present wins, in this order.
REF_COLUMNS = ("post_url", "post url", "url", "permalink", "link", "post_id", "post id", "id")

#: Every canonical metric this module may append, i.e. the buckets the ledger derives from.
KNOWN_METRICS = IMPRESSION_OUTCOMES | CONTENT_ENGAGEMENT_OUTCOMES | CONTENT_CLICK_OUTCOMES


def _norm(key: str) -> str:
    return str(key).strip().lower().replace("-", "_")


def read_export(path: Path) -> list[dict]:
    """Rows from a CSV or JSON analytics export. JSON may be a list, or ``{posts|rows|data: []}``."""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json" or text.lstrip()[:1] in "[{":
        node = json.loads(text)
        if isinstance(node, dict):
            for key in ("posts", "rows", "data", "elements"):
                if isinstance(node.get(key), list):
                    node = node[key]
                    break
            else:
                node = [node]
        return [r for r in node if isinstance(r, dict)]
    return list(csv.DictReader(text.splitlines()))


def row_ref(row: dict) -> str:
    """The post identifier in an export row, by column precedence. ``""`` when absent."""
    lowered = {_norm(k): v for k, v in row.items()}
    for col in REF_COLUMNS:
        value = lowered.get(_norm(col))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _count(value) -> int | None:
    """A non-negative integer count, or None. Strips the thousands separators exports use."""
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text or text in {"-", "—", "n/a", "N/A"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number < 0 or number != int(number):
        return None
    return int(number)


def metrics_in(row: dict) -> tuple[dict[str, int], list[str]]:
    """``({canonical metric: count}, notes)`` for one export row.

    A rate column is dropped with a note (never summed as if it were a count); an unrecognised
    column is reported, not guessed. A recognised column whose value will not parse as a
    non-negative integer is skipped — an absent metric must never be recorded as zero.
    """
    metrics: dict[str, int] = {}
    notes: list[str] = []
    for raw_key, raw_value in row.items():
        key = _norm(raw_key)
        if key in RATE_COLUMNS:
            notes.append(f"dropped rate column {raw_key!r} — the ledger derives rates from counts")
            continue
        metric = METRIC_ALIASES.get(key)
        if metric is None:
            continue
        count = _count(raw_value)
        if count is None:
            notes.append(f"{raw_key!r} = {raw_value!r} is not a count — skipped, not written as 0")
            continue
        metrics[metric] = metrics.get(metric, 0) + count
    return metrics, notes


# ── the attribution chain: export ref → item_id → hook_id ───────────────────────────────


def asset_digest_index(content_root: Path, profile: str) -> dict[str, str]:
    """``content_sha256 → item_id`` over ``content/<profile>/assets/<item-id>.asset.json``.

    How an AUTOMATED publish is attributed: ``agent/publish_dispatch`` records the content hash
    but not the item id, so the item is recovered by recomputing the same digest — the trick
    ``gtm_core.outcomes.attribute`` already uses for a finished video. Both the no-media digest
    and, when a sibling ``finish.json`` carries ``hosted_media_urls``, the with-media digest are
    indexed, because ``content-publish`` may stage either.
    """
    out: dict[str, str] = {}
    assets = content_root / _safe_segment(profile, "profile") / "assets"
    for path in sorted(assets.glob("*.asset.json")):
        try:
            asset = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        body = asset.get("body")
        if not isinstance(body, str) or not body.strip():
            continue
        item_id = path.name[: -len(".asset.json")]
        out.setdefault(content_hash(body, ()), item_id)
        finish = path.with_name(f"{item_id}.finish.json")
        try:
            urls = json.loads(finish.read_text(encoding="utf-8")).get("hosted_media_urls")
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
        if isinstance(urls, list) and urls:
            out.setdefault(content_hash(body, tuple(str(u) for u in urls)), item_id)
    return out


def publish_index(history: list[dict], digests: dict[str, str]) -> dict[str, str]:
    """``post ref → item_id`` from every ``published`` history row.

    A row is indexed under each identifier it carries (``ref``, ``url``, ``post_id``), so an
    export keyed on a permalink and one keyed on a numeric id both join. ``scheduled`` rows are
    skipped on purpose: booked is not live, and it has no metrics.
    """
    out: dict[str, str] = {}
    for row in history:
        if row.get("event") != "published":
            continue
        item_id = row.get("item_id") or digests.get(str(row.get("content_sha256") or ""))
        if not item_id:
            continue
        for key in ("ref", "url", "post_id"):
            value = row.get(key)
            if value is not None and str(value).strip():
                out.setdefault(str(value).strip(), str(item_id))
    return out


def item_tags(plans: list[dict]) -> dict[str, list[str]]:
    """``item_id → learning tags``, from the ContentItems in every plan file.

    Every tag carries its ``key:`` prefix. ``gtm_distill._tag_value`` and
    ``hook_score._compute_prior`` match only ``tag.startswith(f"{prefix}:")``, so a bare tag is
    not read as unknown — it is invisible, and the row lands in that axis's baseline instead of
    its bucket. An axis the item does not carry is omitted, never guessed.
    """
    out: dict[str, list[str]] = {}
    for item in plans:
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            continue
        tags = [
            f"{axis}:{item[axis]}"
            for axis in ("pillar", "journey_stage", "goal", "format")
            if isinstance(item.get(axis), str) and item[axis].strip()
        ]
        if isinstance(item.get("hook_id"), str) and item["hook_id"].strip():
            tags.append(f"hook:{item['hook_id'].strip()}")
        # ``story_format`` follows the same rule as every axis above: present when the item says
        # so, ABSENT otherwise — never ``false``. Absent means "we do not know" (every row written
        # before this existed) and false would mean "we checked, and it is not a story". Collapsing
        # the two destroys the only comparison this tag was added to enable, and does so
        # irreversibly: nothing downstream could tell a migrated row from a measured one.
        brief = item.get("brief")
        if isinstance(brief, dict):
            protagonist = brief.get("protagonist")
            if isinstance(protagonist, str) and protagonist.strip():
                tags.append("story_format:true")
        out[item_id] = tags
    return out


def read_plans(content_root: Path, profile: str) -> list[dict]:
    """Every ContentItem across ``content/<profile>/plans/*.json`` (bare list or ``{items: []}``)."""
    items: list[dict] = []
    plans = content_root / _safe_segment(profile, "profile") / "plans"
    for path in sorted(plans.glob("*.json")):
        try:
            node = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(node, dict):
            node = node.get("items")
        if isinstance(node, list):
            items.extend(x for x in node if isinstance(x, dict))
    return items


def _recorded(existing: list[dict]) -> set[str]:
    """``{ref}:{outcome}`` pairs already on file — the idempotency key."""
    return {
        f"{row.get('ref')}:{row.get('outcome')}"
        for row in existing
        if row.get("ref") and row.get("outcome")
    }


def plan_rows(
    export_rows: list[dict],
    existing: list[dict],
    *,
    refs_to_items: dict[str, str],
    tags_by_item: dict[str, list[str]],
    channel: str,
    fetched: str,
) -> tuple[list[dict], list[str]]:
    """Outcome rows for every attributable export row, plus the notes a reader needs. Pure."""
    rows: list[dict] = []
    notes: list[str] = []
    seen = _recorded(existing)
    for export_row in export_rows:
        ref = row_ref(export_row)
        if not ref:
            notes.append(
                f"REFUSED: an export row carries none of {list(REF_COLUMNS)} — cannot be attributed"
            )
            continue
        item_id = refs_to_items.get(ref)
        if item_id is None:
            notes.append(
                f"REFUSED: {ref} matches no `published` row in history.jsonl — record the publish "
                "first (ledger_cli record-manual-publish --ref) rather than tagging this by hand"
            )
            continue
        tags = tags_by_item.get(item_id)
        if tags is None:
            notes.append(f"REFUSED: {ref} → item {item_id}, which is in no plan file — no tags")
            continue
        if not any(t.startswith("hook:") for t in tags):
            notes.append(
                f"{ref} → item {item_id} has no hook_id: rows appended, but they can never feed a "
                "hook prior. Assign a hook_id at Gate 1 (content-plan) so the next post can."
            )
        metrics, row_notes = metrics_in(export_row)
        notes.extend(f"{ref}: {n}" for n in row_notes)
        if not metrics:
            notes.append(f"REFUSED: {ref} carries no recognised count column — nothing written")
            continue
        for metric, value in sorted(metrics.items()):
            if f"{ref}:{metric}" in seen:
                notes.append(
                    f"{ref}: {metric} already recorded — skipped. Metrics change as a post ages; "
                    "a second row would be SUMMED into the first, not replace it."
                )
                continue
            rows.append(
                {
                    "channel": channel,
                    "outcome": metric,
                    "ref": ref,
                    "value": value,
                    "ts": f"{fetched}T00:00:00Z",
                    "tags": list(tags),
                    "meta": {"item_id": item_id, "source": SOURCE, "fetched": fetched},
                }
            )
    return rows, notes


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Record platform analytics as content outcome rows (impressions, "
        "reactions, …) tagged hook:<id> — the producer hook_score's prior reads."
    )
    ap.add_argument("--profile", required=True)
    ap.add_argument("--content-root", default=None)
    ap.add_argument(
        "--export",
        required=True,
        nargs="+",
        help="operator-exported post analytics (CSV or JSON), one file per channel",
    )
    ap.add_argument(
        "--channel", required=True, help="the platform: linkedin | x | instagram | youtube"
    )
    ap.add_argument(
        "--fetched", default="", help="YYYY-MM-DD the export was taken (default: today)"
    )
    ap.add_argument(
        "--apply", action="store_true", help="append to outcomes.jsonl (default: dry run)"
    )
    args = ap.parse_args(argv)

    root = Path(args.content_root) if args.content_root else resolve_content_root()
    profile = args.profile
    fetched = args.fetched or datetime.date.today().isoformat()

    export_rows: list[dict] = []
    for path in args.export:
        export_rows.extend(read_export(Path(path)))

    history_path = root / _safe_segment(profile, "profile") / "history.jsonl"
    history: list[dict] = []
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            try:
                history.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    rows, notes = plan_rows(
        export_rows,
        read_outcomes(root, profile),
        refs_to_items=publish_index(history, asset_digest_index(root, profile)),
        tags_by_item=item_tags(read_plans(root, profile)),
        channel=args.channel,
        fetched=fetched,
    )
    for row in rows:
        print(("APPEND " if args.apply else "DRY    ") + json.dumps(row, ensure_ascii=False))
    for note in notes:
        print(f"  ! {note}")
    if args.apply:
        for row in rows:
            append_outcome(root, profile, row)
        print(f"\n{len(rows)} content outcome row(s) appended")
    else:
        print(f"\n{len(rows)} content outcome row(s) planned (dry run)")

    hook_rows = sum(1 for r in rows if any(t.startswith("hook:") for t in r["tags"]))
    impressions = sum(1 for r in rows if r["outcome"] in IMPRESSION_OUTCOMES)
    print(
        f"{hook_rows} carry a hook: tag ({impressions} of them impression-bucket rows) — "
        "those are the only ones hook_score's prior and hooks.is_fatigued can read."
    )
    return 1 if [n for n in notes if n.startswith("REFUSED")] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
