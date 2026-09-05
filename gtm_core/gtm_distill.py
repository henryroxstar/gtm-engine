"""GTM outcome distiller — the learning tier of the closed loop (PRD Phase 4).

The GTM analogue of ``/lesson-distill``: reads the outcomes ledger (the capture tier,
``gtm_core.outcomes``), correlates results by **tag** (the angle/hook/persona/segment axis), and
writes a human-readable learnings note ``content/<profile>/learnings/<period>.md`` with a
``## Promote?`` section — candidate edits to the knowledge corpus (``hook-matrix.md``, ``voice.md``,
``case-studies.md``) that an operator applies by hand. It **owns** (overwrites) its own note and only
**reads** the tier below (outcomes.jsonl); it never edits the live corpus — closing the loop that
lets skills improve from results (pain #7).

Deterministic + files-only: a promote-worthy learning is one with enough observations and a clear
rate lift over baseline (the GTM analogue of lesson-distill's "appeared 2+ times"). Reusable across
every profile — takes ``content_root`` + ``profile``, never a global.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Collection
from datetime import UTC, date, datetime
from pathlib import Path

from . import hooks as hk
from . import knowledge_staging
from . import outcomes as oc
from . import tweet_patterns as tp
from .paths import resolve_content_root, resolve_profiles_root

#: A tag needs at least this many sends before its rate is trustworthy enough to promote.
DEFAULT_MIN_SENT = 5
#: A tag's reply rate must beat (or trail) the baseline by this factor to be a promote candidate.
DEFAULT_LIFT = 1.3


def _period_label(today: date) -> str:
    return today.strftime("%Y-%m")


def _rate(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def promote_candidates(
    summary: dict, *, min_sent: int = DEFAULT_MIN_SENT, lift: float = DEFAULT_LIFT
) -> list[dict]:
    """Tags that clearly out- or under-perform the baseline reply rate with enough observations."""
    baseline = summary["totals"].get("reply_rate")
    if not baseline:
        return []
    out = []
    for tag, b in summary["by_tag"].items():
        rate = b.get("reply_rate")
        if rate is None or b["sent"] < min_sent:
            continue
        if rate >= baseline * lift:
            out.append({"tag": tag, "direction": "outperforms", "rate": rate, "sent": b["sent"]})
        elif rate <= baseline / lift:
            out.append({"tag": tag, "direction": "underperforms", "rate": rate, "sent": b["sent"]})
    out.sort(key=lambda c: (c["direction"], -c["rate"]))
    return out


def render_learnings(profile: str, period: str, summary: dict, candidates: list[dict]) -> str:
    t = summary["totals"]
    lines = [
        f"# GTM learnings — {profile} — {period}",
        "",
        "> Distilled from `content/"
        + profile
        + "/outcomes.jsonl` by `gtm_core.gtm_distill`. This is"
        " an analysis note, not the corpus — apply the promote candidates below to the named"
        " knowledge topics by hand.",
        "",
        f"**Baseline:** reply rate {_rate(t['reply_rate'])}, meeting rate {_rate(t['meeting_rate'])} "
        f"(sent {t['sent']:g}, replies {t['replies']:g}, meetings {t['meetings']:g}).",
        "",
        "## By channel",
        "",
        "| Channel | Sent | Replies | Reply rate | Meeting rate |",
        "|---|---|---|---|---|",
    ]
    for ch, b in summary["by_channel"].items():
        lines.append(
            f"| {ch} | {b['sent']:g} | {b['replies']:g} | {_rate(b['reply_rate'])} | {_rate(b['meeting_rate'])} |"
        )

    lines += [
        "",
        "## By tag (angle / persona / segment)",
        "",
        "| Tag | Sent | Replies | Reply rate |",
        "|---|---|---|---|",
    ]
    ranked = sorted(
        summary["by_tag"].items(),
        key=lambda kv: (kv[1]["reply_rate"] is None, -(kv[1]["reply_rate"] or 0)),
    )
    for tag, b in ranked:
        lines.append(f"| `{tag}` | {b['sent']:g} | {b['replies']:g} | {_rate(b['reply_rate'])} |")

    lines += ["", "## Promote?", ""]
    if candidates:
        lines.append(
            "Clear signals worth folding into the knowledge corpus (apply by hand to the named "
            "topic, then re-stamp its `refreshed:`):"
        )
        lines.append("")
        for c in candidates:
            topic = (
                "hook-matrix.md" if c["direction"] == "outperforms" else "hook-matrix.md / voice.md"
            )
            verb = "strengthen" if c["direction"] == "outperforms" else "reconsider / soften"
            lines.append(
                f"- `{c['tag']}` **{c['direction']}** — reply rate {_rate(c['rate'])} "
                f"(n={c['sent']:g}). {verb.capitalize()} this angle in `{topic}`."
            )
    else:
        lines.append("_No signal yet clears the promote threshold (need more observations)._")
    lines.append("")
    return "\n".join(lines)


def distill(
    content_root: Path,
    profile: str,
    *,
    period: str | None = None,
    today: date | None = None,
    stage: bool = False,
    profiles_root: Path | None = None,
) -> Path:
    """Write ``content/<profile>/learnings/<period>.md`` from the outcomes ledger. Returns its path.

    With ``stage=True`` also stages a ``learnings`` knowledge candidate via ``knowledge_staging`` so
    the operator can promote it through the standard Phase-3 flow (otherwise the note's ``Promote?``
    section is applied to existing topics by hand)."""
    today = today or date.today()
    period = period or _period_label(today)
    rows = oc.read_outcomes(content_root, profile)
    summary = oc.summarize(rows)
    candidates = promote_candidates(summary)
    note = render_learnings(profile, period, summary, candidates)

    target = content_root / profile / "learnings" / f"{period}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(note, encoding="utf-8")

    if stage and candidates:
        knowledge_staging.stage(content_root, profile, "learnings", note)
    return target


def summarize_content(
    content_root: Path,
    profile: str,
    *,
    profiles_root: Path | None = None,
    period: str | None = None,
    since_month: str | None = None,
    min_impressions: float = 500,
    lift: float = 1.3,
) -> dict:
    """Aggregate content outcomes by hook × format × platform, and separately by
    X tweet-pattern × format × platform.

    Returns a dict with ``baseline``, ``by_hook``, ``promote_candidates``, ``demote_candidates``,
    ``fatigued_hooks``, ``pattern_baseline``, and ``by_pattern``. The two axes are independent:
    ``by_pattern`` is keyed on the ``pattern:<id>`` tag alone and does not require a row to also
    carry a ``hook:`` tag, and ``pattern_baseline`` (rows with no ``pattern:`` tag) is a distinct
    population from ``baseline`` (rows with no ``hook:`` tag) — reusing one for the other would
    compare a pattern's lift against a population it was never a subset of. Rows whose
    ``pattern:`` value isn't in the committed tweet-pattern registry are dropped from
    ``by_pattern`` (mirrors the existing unknown-hook-id drop below). Does NOT write files — use
    :func:`write_content_models` to persist the JSON model files.
    """
    profiles_root = profiles_root or resolve_profiles_root()
    rows = oc.read_outcomes(content_root, profile, since_month=since_month)

    bank = hk.load_hooks(profiles_root, profile, content_root=content_root)
    hook_ids = {h.id for h in bank.hooks}

    # Five learning axes, one helper. Each computes its OWN baseline (rows carrying no tag for
    # that axis) — reusing one axis's baseline for another would compare a bucket's lift against
    # a population it was never a subset of.
    #
    # The two registry-backed axes pass `valid=`; the three portfolio axes deliberately do not.
    # `journey_stage` and `goal` are engine-fixed enums in schemas/content-item.schema.json and
    # `pillar` is tenant config, so an unexpected value there is a data bug worth seeing in the
    # output, not a row to hide.
    baseline, by_hook = _by_axis(rows, lambda r: _tag_value(r, "hook"), valid=hook_ids)
    pattern_baseline, by_pattern = _by_axis(
        rows, lambda r: _tag_value(r, "pattern"), valid=tp.pattern_ids()
    )
    journey_baseline, by_journey_stage = _by_axis(rows, lambda r: _tag_value(r, "journey_stage"))
    goal_baseline, by_goal = _by_axis(rows, lambda r: _tag_value(r, "goal"))
    pillar_baseline, by_pillar = _by_axis(rows, lambda r: _tag_value(r, "pillar"))

    promote = _promote_candidates(by_hook, baseline, min_impressions=min_impressions, lift=lift)
    demote = _demote_candidates(by_hook, baseline, min_posts=3, lift=lift)
    fatigued = [
        {
            "hook_id": h.id,
            "max_impressions": h.max_impressions,
            "fatigue_window_days": h.fatigue_window_days,
        }
        for h in bank.hooks
        if h.status != "candidate" and hk.is_fatigued(content_root, profile, h.id, bank)
    ]

    today = date.today()
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period": period or _period_label(today),
        "baseline": baseline,
        "by_hook": dict(sorted(by_hook.items())),
        "promote_candidates": promote,
        "demote_candidates": demote,
        "fatigued_hooks": fatigued,
        "pattern_baseline": pattern_baseline,
        "by_pattern": dict(sorted(by_pattern.items())),
        "axes": {
            "journey_stage": {
                "baseline": journey_baseline,
                "by_value": dict(sorted(by_journey_stage.items())),
            },
            "goal": {"baseline": goal_baseline, "by_value": dict(sorted(by_goal.items()))},
            "pillar": {"baseline": pillar_baseline, "by_value": dict(sorted(by_pillar.items()))},
        },
    }


def write_content_models(
    content_root: Path,
    profile: str,
    summary: dict,
) -> dict[str, Path]:
    """Persist ``summary`` to ``content/<profile>/models/*.json``."""
    models_dir = content_root / profile / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "hook_performance": models_dir / "hook_performance.json",
        "promote_candidates": models_dir / "promote_candidates.json",
        "demote_candidates": models_dir / "demote_candidates.json",
        "fatigued_hooks": models_dir / "fatigued_hooks.json",
        "pattern_performance": models_dir / "pattern_performance.json",
        "axis_performance": models_dir / "axis_performance.json",
    }
    paths["hook_performance"].write_text(
        json.dumps(
            {
                "generated_at": summary["generated_at"],
                "period": summary["period"],
                "baseline": summary["baseline"],
                "by_hook": summary["by_hook"],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    paths["promote_candidates"].write_text(
        json.dumps(summary["promote_candidates"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    paths["demote_candidates"].write_text(
        json.dumps(summary["demote_candidates"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    paths["fatigued_hooks"].write_text(
        json.dumps(summary["fatigued_hooks"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    paths["pattern_performance"].write_text(
        json.dumps(
            {
                "generated_at": summary["generated_at"],
                "period": summary["period"],
                "baseline": summary["pattern_baseline"],
                "by_pattern": summary["by_pattern"],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # One file for the three portfolio axes, not three files: cockpit/hooks.py locates models by
    # filename and a contract test asserts model filenames appear in the skill body, so every new
    # filename is a new contract surface. These axes are also read together — the question is
    # "what mix shipped", not "how did journey_stage do".
    paths["axis_performance"].write_text(
        json.dumps(
            {
                "generated_at": summary["generated_at"],
                "period": summary["period"],
                **summary["axes"],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return paths


def distill_content(
    content_root: Path,
    profile: str,
    *,
    profiles_root: Path | None = None,
    period: str | None = None,
    since_month: str | None = None,
    min_impressions: float = 500,
    lift: float = 1.3,
) -> dict[str, Path]:
    """Run the content distiller and write model files."""
    summary = summarize_content(
        content_root,
        profile,
        profiles_root=profiles_root,
        period=period,
        since_month=since_month,
        min_impressions=min_impressions,
        lift=lift,
    )
    return write_content_models(content_root, profile, summary)


# --- content distiller helpers ------------------------------------------------


def _blank_content_bucket() -> dict:
    return {
        "posts": 0,
        "impressions": 0.0,
        "content_engagements": 0.0,
        "clicks": 0.0,
        "retention_seconds": 0.0,
        "counts": {},
        "predictor_bands": {},
    }


def _add_content(bucket: dict, row: dict) -> None:
    outcome = str(row.get("outcome", "")).strip().lower()
    if not outcome or outcome == "hook_score":
        return
    try:
        value = float(row.get("value", 1) or 0)
    except (TypeError, ValueError):
        value = 1.0

    bucket["counts"][outcome] = bucket["counts"].get(outcome, 0) + value

    if outcome == "published":
        bucket["posts"] += value
    if outcome in oc.IMPRESSION_OUTCOMES:
        bucket["impressions"] += value
    if outcome in oc.CONTENT_ENGAGEMENT_OUTCOMES:
        bucket["content_engagements"] += value
    if outcome in oc.CONTENT_CLICK_OUTCOMES:
        bucket["clicks"] += value
    if outcome == "retention_seconds":
        bucket["retention_seconds"] += value

    tags = row.get("tags") or []
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag.startswith("predictor_band:"):
                band = tag.split(":", 1)[1]
                bucket["predictor_bands"][band] = bucket["predictor_bands"].get(band, 0) + 1


def _finalize_content(bucket: dict) -> dict:
    impressions = bucket["impressions"]
    posts = bucket["posts"]
    bucket["engagement_rate"] = (
        round(bucket["content_engagements"] / impressions, 4) if impressions else None
    )
    bucket["click_rate"] = round(bucket["clicks"] / impressions, 4) if impressions else None
    bucket["avg_retention_seconds"] = (
        round(bucket["retention_seconds"] / posts, 2) if posts else None
    )
    return bucket


def _aggregate_content_rows(rows: list[dict]) -> dict:
    bucket = _blank_content_bucket()
    for row in rows:
        _add_content(bucket, row)
    return bucket


def _by_axis(
    rows: list[dict],
    key_of: Callable[[dict], str | None],
    *,
    valid: Collection[str] | None = None,
) -> tuple[dict, dict[str, dict]]:
    """Aggregate content rows along one learning axis. Returns ``(baseline, buckets)``.

    ``key_of`` maps a row to its value on this axis, or ``None`` when the row does not carry it.
    ``valid``, when given, is a closed vocabulary: rows whose key falls outside it are dropped.
    Pass it only for registry-backed axes (hook bank, tweet-pattern registry).

    Three properties this centralises, each previously restated per axis:

    * **The baseline is per-axis** — rows where ``key_of`` is ``None``. A hook's lift is measured
      against untagged-by-hook rows, a pattern's against untagged-by-pattern rows. These are
      different populations and must not be shared.
    * **A row with a present-but-invalid key belongs to neither** the buckets nor the baseline.
      That is deliberate and pre-existing: the baseline tests tag *presence* while the bucket
      tests *validity*, so an unknown hook id is excluded from both rather than silently
      inflating the population it is being compared against.
    * Buckets are keyed ``<value> -> {"totals", "by_format_platform"}`` with the
      ``f"{fmt}|{platform}"`` composite key, finalized (rates derived) on the way out.
    """
    baseline = _finalize_content(_aggregate_content_rows([r for r in rows if key_of(r) is None]))

    buckets: dict[str, dict] = {}
    for row in rows:
        key = key_of(row)
        if key is None or (valid is not None and key not in valid):
            continue
        fmt = _tag_value(row, "format") or "unknown"
        platform = _tag_value(row, "platform") or str(row.get("channel", "unknown")).strip().lower()
        fp_key = f"{fmt}|{platform}"

        bucket = buckets.setdefault(
            key, {"totals": _blank_content_bucket(), "by_format_platform": {}}
        )
        bucket["by_format_platform"].setdefault(fp_key, _blank_content_bucket())
        _add_content(bucket["totals"], row)
        _add_content(bucket["by_format_platform"][fp_key], row)

    for data in buckets.values():
        data["totals"] = _finalize_content(data["totals"])
        data["by_format_platform"] = {
            k: _finalize_content(v) for k, v in data["by_format_platform"].items()
        }

    return baseline, buckets


def _tag_value(row: dict, prefix: str) -> str | None:
    tags = row.get("tags") or []
    if not isinstance(tags, list):
        return None
    for tag in tags:
        if isinstance(tag, str) and tag.startswith(f"{prefix}:"):
            return tag.split(":", 1)[1]
    return None


def _promote_candidates(
    by_hook: dict,
    baseline: dict,
    *,
    min_impressions: float,
    lift: float,
) -> list[dict]:
    baseline_rate = baseline.get("engagement_rate")
    if baseline_rate is None:
        return []
    out = []
    for hook_id, data in by_hook.items():
        totals = data["totals"]
        rate = totals.get("engagement_rate")
        impressions = totals.get("impressions", 0)
        if rate is None or impressions < min_impressions:
            continue
        if rate >= baseline_rate * lift:
            out.append(
                {
                    "hook_id": hook_id,
                    "engagement_rate": rate,
                    "baseline_rate": baseline_rate,
                    "impressions": impressions,
                    "posts": totals.get("posts", 0),
                    "direction": "outperforms",
                }
            )
    out.sort(key=lambda c: -c["engagement_rate"])
    return out


def _demote_candidates(
    by_hook: dict,
    baseline: dict,
    *,
    min_posts: int,
    lift: float,
) -> list[dict]:
    baseline_rate = baseline.get("engagement_rate")
    if baseline_rate is None:
        return []
    out = []
    for hook_id, data in by_hook.items():
        totals = data["totals"]
        rate = totals.get("engagement_rate")
        posts = totals.get("posts", 0)
        if rate is None or posts < min_posts:
            continue
        if rate <= baseline_rate / lift:
            out.append(
                {
                    "hook_id": hook_id,
                    "engagement_rate": rate,
                    "baseline_rate": baseline_rate,
                    "impressions": totals.get("impressions", 0),
                    "posts": posts,
                    "direction": "underperforms",
                }
            )
    out.sort(key=lambda c: c["engagement_rate"])
    return out


# --- CLI ----------------------------------------------------------------------


def _all_profiles(profiles_root: Path) -> list[str]:
    if not profiles_root.is_dir():
        return []
    return sorted(
        c.name for c in profiles_root.iterdir() if c.is_dir() and (c / "PROFILE.md").is_file()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.gtm_distill",
        description="Distill the outcomes ledger into per-profile learnings and content models.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    dist_p = sub.add_parser(
        "distill", help="write outreach learnings note (content/<profile>/learnings/<period>.md)"
    )
    dist_p.add_argument("--profile", default=None)
    dist_p.add_argument("--all", action="store_true")
    dist_p.add_argument("--period", default=None, help="note label (default current YYYY-MM)")
    dist_p.add_argument(
        "--stage", action="store_true", help="also stage a learnings knowledge candidate"
    )
    dist_p.add_argument("--content-root", default=None)

    content_p = sub.add_parser(
        "distill-content",
        help="write content-performance model files (content/<profile>/models/*.json)",
    )
    content_p.add_argument("--profile", default=None)
    content_p.add_argument("--all", action="store_true")
    content_p.add_argument("--period", default=None, help="model label (default current YYYY-MM)")
    content_p.add_argument(
        "--since-month", default=None, help="filter outcomes to a YYYY-MM window"
    )
    content_p.add_argument(
        "--min-impressions", type=float, default=500, help="promote threshold (default 500)"
    )
    content_p.add_argument(
        "--lift", type=float, default=1.3, help="promote/demote lift factor (default 1.3)"
    )
    content_p.add_argument("--content-root", default=None)

    args = parser.parse_args(argv)

    content_root = (
        Path(args.content_root).expanduser().resolve()
        if args.content_root
        else resolve_content_root()
    )

    if args.command == "distill":
        if args.all:
            profiles = _all_profiles(resolve_profiles_root())
        elif args.profile:
            profiles = [args.profile]
        else:
            raise SystemExit("[gtm-distill] pass --profile <slug> or --all")
        for profile in profiles:
            path = distill(content_root, profile, period=args.period, stage=args.stage)
            print(f"wrote {path}")
        return 0

    # distill-content
    if args.all:
        profiles = _all_profiles(resolve_profiles_root())
    elif args.profile:
        profiles = [args.profile]
    else:
        raise SystemExit("[gtm-distill] pass --profile <slug> or --all")
    for profile in profiles:
        paths = distill_content(
            content_root,
            profile,
            period=args.period,
            since_month=args.since_month,
            min_impressions=args.min_impressions,
            lift=args.lift,
        )
        for name, path in paths.items():
            print(f"wrote {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
