"""Deterministic scoring, heat calculation, and ranking for prospect candidates.

Implements Step 8 of the prospect skill (PRD 2026-09-20). This CLI is deliberately
profile-blind: the tenant's ICP rubric is prose the agent applies itself, producing a
per-candidate rubric fit score (`fit_score`, else the stored `base_score`, else `score`) that
the caller supplies. Everything downstream of that number is owned here —
- Adds intent "heat" (+2 when any intent feed scores >= 75; +1 more for double-intent —
  two or more DISTINCT feeds >= 75; a 60-74 feed is "elevated" but earns no heat)
- Caps the heat-adjusted score at the per-segment ceiling
- Assigns a tier (A / B / drop) and priority
- Ranks finalists by: (1) Tier A first, (2) heat descending, (3) new_in_role,
  (4) most recent signal_observed, (5) score descending
- Splits publishable (Tier A/B) rows from below-threshold ("drop") rows

Stdlib-only, deterministic, zero egress.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from gtm_core.prospects_merge import is_blank
from gtm_core.score_heat import _evaluate_heat_detailed, _parse_signal_date_ordinal, evaluate_heat

# `evaluate_heat` moved to gtm_core.score_heat (§R10 split); re-imported and listed in
# __all__ so `from gtm_core.score_prospects import evaluate_heat` keeps resolving.
__all__ = [
    "BELOW_THRESHOLD_REASON",
    "DEFAULT_CEILINGS",
    "DEFAULT_PUBLISH_THRESHOLD",
    "DEFAULT_TIER_A_THRESHOLDS",
    "evaluate_heat",
    "main",
    "score_and_rank_prospects",
    "score_and_rank_prospects_with_dropped",
    "score_candidate",
    "sort_key_finalist",
]

# Default ceilings per segment (from gates-and-scoring.md)
DEFAULT_CEILINGS: dict[str, int] = {
    "enterprise": 12,
    "startup": 10,
}

# Default Tier-A thresholds (~70% of ceiling)
DEFAULT_TIER_A_THRESHOLDS: dict[str, int] = {
    "enterprise": 8,
    "startup": 7,
}

DEFAULT_PUBLISH_THRESHOLD: int = 6

#: The reason this scorer writes beside its own ``verdict: drop``. A local copy keeps the
#: module stdlib-only; pinned equal to ``prospects_item.BELOW_THRESHOLD_REASON`` by
#: ``tests/unit/test_score_prospects_corrections.py``.
BELOW_THRESHOLD_REASON = "below publish threshold"

#: The tier a CATEGORISED row carries (``gtm_core.scorecard`` could not score it because an input
#: was absent). Deliberately not "drop": a drop is a finding about the account, and this is a
#: finding about our research. Rows with this tier are neither published nor ranked — they are a
#: third bucket, because sweeping them into ``dropped`` would stamp them ``verdict: drop`` with
#: reason "below publish threshold", which is a sentence about a number they never had.
UNSCORED_TIER = "unscored"

_TRUE_WORDS = frozenset({"true", "yes", "1"})


def _resolve_segment_params(
    segment: str,
    ceiling: int | None,
    tier_a_threshold: int | None,
    warned_segments: set[str] | None,
) -> tuple[int, int]:
    """Resolve the ceiling and Tier-A threshold for a segment.

    An unrecognised segment still gets a value (the startup-shaped default), but PSK-027
    means that fallback is never silent: the first row of a distinct unknown segment in a
    run prints one warning naming what was used.
    """
    ceiling_fell_back = ceiling is None and segment not in DEFAULT_CEILINGS
    tier_a_fell_back = tier_a_threshold is None and segment not in DEFAULT_TIER_A_THRESHOLDS

    c = ceiling if ceiling is not None else DEFAULT_CEILINGS.get(segment, 10)
    t_a = (
        tier_a_threshold
        if tier_a_threshold is not None
        else DEFAULT_TIER_A_THRESHOLDS.get(segment, round(c * 0.7))
    )

    if (ceiling_fell_back or tier_a_fell_back) and warned_segments is not None:
        if segment not in warned_segments:
            warned_segments.add(segment)
            print(
                f"Warning: unknown segment {segment!r} — falling back to "
                f"ceiling={c}, tier_a_threshold={t_a}",
                file=sys.stderr,
            )

    return c, t_a


def _coerce_finite_score(value: Any, label: str) -> float:
    """Validate a rubric fit score is a real, finite number (PSK-009, §R5).

    Untrusted (LLM-authored) input must never be silently scored 0 — a non-numeric or
    non-finite (NaN/inf) value is a defect to surface, not a value to guess past.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"candidate {label!r} has a non-numeric score: {value!r}") from exc
    if math.isnan(numeric) or math.isinf(numeric):
        raise ValueError(f"candidate {label!r} has a non-finite score: {value!r}") from None
    return numeric


def _format_score(value: float) -> int | float:
    return int(value) if value.is_integer() else round(value, 1)


def _parse_bool(value: Any) -> bool:
    """``"false"`` is False. Real bools pass through; ``true``/``yes``/``1`` are True; anything
    else — a list, ``"maybe"`` — is False, never a crash (§R5: the row is LLM-authored)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | str):
        return str(value).strip().lower() in _TRUE_WORDS
    return False


def _rubric_score(res: dict[str, Any], label: str) -> float:
    """The pre-heat rubric score, from ``base_score`` | ``fit_score`` | ``score``.

    ``base_score`` is what this scorer wrote last time and ``fit_score`` is what the researcher
    supplies. When both are present and DIFFER, the researcher has corrected the rubric score
    since the last pass, and the correction wins — it used to be ignored forever. When they
    agree nothing was corrected, and either one is the same number. ``score`` is read last: on
    a scored row it already has heat in it (PSK-003).
    """
    stored, supplied = res.get("base_score"), res.get("fit_score")
    if supplied is not None:
        return _coerce_finite_score(supplied, label)
    if stored is not None:
        return _coerce_finite_score(stored, label)
    fallback = res.get("score")
    return _coerce_finite_score(0 if fallback is None else fallback, label)


def score_candidate(
    candidate: dict[str, Any],
    publish_threshold: int = DEFAULT_PUBLISH_THRESHOLD,
    tier_a_threshold: int | None = None,
    ceiling: int | None = None,
    *,
    warned_segments: set[str] | None = None,
) -> dict[str, Any]:
    """Score a single candidate record and assign heat, total score, tier, and priority.

    The caller supplies the rubric fit score (see `_rubric_score` for the preference order).
    `base_score` is always written back holding the pre-heat value, so re-scoring an
    already-scored row is idempotent (PSK-003) instead of compounding heat.
    """
    if not isinstance(candidate, dict):
        raise ValueError(f"candidate is not an object (got {type(candidate).__name__})")

    res = dict(candidate)
    segment = (res.get("segment") or "startup").lower()
    label = res.get("company") or res.get("domain") or "<unknown>"

    if not is_blank(res.get("score_category")):
        # A CATEGORISED row (gtm_core.scorecard): its inputs were absent, so there is no number
        # to compute and never was. It keeps its category, takes tier `unscored`, and carries NO
        # `score` key at all — a `None` here would crash sort_key_finalist's float(), and a 0
        # would read downstream as "scored, and weak", which is the exact confusion this tier
        # exists to prevent.
        res.pop("score", None)
        res.pop("base_score", None)
        res["tier"] = UNSCORED_TIER
        res["priority"] = "low"
        res["heat"] = int(res.get("heat") or 0)
        res["new_in_role"] = _parse_bool(res.get("new_in_role"))
        return res

    c, t_a = _resolve_segment_params(segment, ceiling, tier_a_threshold, warned_segments)

    base_score_num = _rubric_score(res, label)

    intent_data = res.get("intent_scores") or res.get("intent_topics")
    intent_feeds = res.get("intent_feeds")
    top_intent_score = res.get("top_intent_score")

    heat, active_feeds, is_elevated, topics_fired, unscored = _evaluate_heat_detailed(
        intent_data=intent_data,
        intent_feeds=intent_feeds,
        top_intent_score=top_intent_score,
    )

    if active_feeds:
        res["intent_feeds"] = active_feeds
    elif "intent_feeds" not in res:
        res["intent_feeds"] = []

    if topics_fired:
        res["intent_topics_fired"] = topics_fired

    res["heat"] = heat
    if is_elevated and heat == 0:
        res["intent_elevated"] = True
    if unscored:
        res["intent_unscored"] = True

    res["base_score"] = _format_score(base_score_num)
    total_score = min(base_score_num + heat, float(c))
    res["score"] = _format_score(total_score)

    # Assign Tier and Priority
    if res["score"] >= t_a:
        res["tier"] = "A"
        res["priority"] = "high"
    elif res["score"] >= publish_threshold:
        res["tier"] = "B"
        res["priority"] = "medium"
    else:
        res["tier"] = "drop"
        res["priority"] = "low"

    # Ensure boolean new_in_role
    res["new_in_role"] = _parse_bool(res.get("new_in_role"))

    return res


def sort_key_finalist(item: dict[str, Any]) -> tuple[int, int, int, int, int, float]:
    """Sort key for finalist queue:
    1. Tier priority (Tier A first, then Tier B, then drop)
    2. Heat descending (3, 2, 1, 0)
    3. new_in_role (True before False)
    4/5. signal_observed recency — dated rows before undated ones (PSK-006); newest first
       among dated rows
    6. score descending
    """
    tier = str(item.get("tier", "")).upper()
    if tier == "A":
        tier_rank = 0
    elif tier == "B":
        tier_rank = 1
    else:
        tier_rank = 2

    heat = int(item.get("heat", 0))
    new_in_role = 1 if item.get("new_in_role") else 0

    ordinal = _parse_signal_date_ordinal(item.get("signal_observed"))
    if ordinal is None:
        date_rank, date_component = 1, 0
    else:
        date_rank, date_component = 0, -ordinal

    score = float(item.get("score", 0))

    return (tier_rank, -heat, -new_in_role, date_rank, date_component, -score)


def score_and_rank_prospects_with_dropped(
    candidates: list[dict[str, Any]],
    publish_threshold: int = DEFAULT_PUBLISH_THRESHOLD,
    tier_a_thresholds: dict[str, int] | None = None,
    ceilings: dict[str, int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Score every candidate; return (published, dropped, unscored) — three buckets.

    `published` (Tier A/B) is ranked. `dropped` (below the publish threshold) keeps
    `tier == "drop"` and gains `verdict`/`verdict_reason` so the refusal stays recordable
    (PSK-008) even though it never reaches the finalist queue.
    """
    warned_segments: set[str] = set()
    scored: list[dict[str, Any]] = []
    for index, cand in enumerate(candidates):
        if not isinstance(cand, dict):
            raise ValueError(
                f"candidate at index {index} is not an object (got {type(cand).__name__})"
            )
        seg = (cand.get("segment") or "startup").lower()
        t_a = tier_a_thresholds.get(seg) if tier_a_thresholds else None
        ceil = ceilings.get(seg) if ceilings else None

        scored.append(
            score_candidate(
                cand,
                publish_threshold=publish_threshold,
                tier_a_threshold=t_a,
                ceiling=ceil,
                warned_segments=warned_segments,
            )
        )

    # THREE buckets. A categorised row was never scored, so "below publish threshold" is not
    # true of it and `verdict: drop` would close a question nobody asked.
    published = [row for row in scored if row.get("tier") in ("A", "B")]
    unscored = [row for row in scored if row.get("tier") == UNSCORED_TIER]
    dropped = [
        row
        for row in scored
        if row.get("tier") not in ("A", "B") and row.get("tier") != UNSCORED_TIER
    ]
    for row in dropped:
        if row.get("verdict") != "drop":  # a researcher's own drop keeps the reason it gave
            row["verdict_reason"] = BELOW_THRESHOLD_REASON
        row["verdict"] = "drop"
    for row in published:
        # A row this scorer once refused and now publishes (a corrected fit score) must not
        # carry that refusal on: import reads `verdict: drop` as final. Only the scorer's OWN
        # refusal is cleared — a drop with any other reason is the researcher's.
        if row.get("verdict") == "drop" and row.get("verdict_reason") == BELOW_THRESHOLD_REASON:
            del row["verdict"], row["verdict_reason"]

    # Categorised rows are NOT sorted into the finalist queue: ranking them would place "we
    # never researched this" alongside "we researched this and it is weak", which is the one
    # confusion this whole tier exists to remove.
    published.sort(key=sort_key_finalist)
    return published, dropped, unscored


def score_and_rank_prospects(
    candidates: list[dict[str, Any]],
    publish_threshold: int = DEFAULT_PUBLISH_THRESHOLD,
    tier_a_thresholds: dict[str, int] | None = None,
    ceilings: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Score all candidates and rank the publishable ones (Tier A/B only — PSK-008).

    Use `score_and_rank_prospects_with_dropped` (or the CLI's `--dropped-out`) to also
    recover the below-threshold rows.
    """
    published, _dropped, unscored = score_and_rank_prospects_with_dropped(
        candidates,
        publish_threshold=publish_threshold,
        tier_a_thresholds=tier_a_thresholds,
        ceilings=ceilings,
    )
    if unscored:
        # This helper returns ONE list, so it has nowhere to put a categorised row. Refusing is
        # the only answer that does not silently shrink the denominator — a caller that scores
        # categorised rows must read all three buckets.
        raise ValueError(
            f"{len(unscored)} categorised row(s) have no place in a single ranked list — call "
            "score_and_rank_prospects_with_dropped() and handle the third (unscored) bucket"
        )
    return published


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.score_prospects",
        description="Deterministic scoring and finalist ranking for prospect candidates.",
    )
    parser.add_argument(
        "--items",
        "--candidates",
        dest="items",
        required=True,
        help="Path to JSON file with candidate objects, or '-' for stdin",
    )
    parser.add_argument(
        "--publish-threshold",
        type=int,
        default=DEFAULT_PUBLISH_THRESHOLD,
        help=f"Minimum score to publish as Tier B (default: {DEFAULT_PUBLISH_THRESHOLD})",
    )
    parser.add_argument(
        "--tier-a-threshold",
        type=int,
        default=None,
        help="Tier-A score threshold override",
    )
    parser.add_argument(
        "--ceiling",
        type=int,
        default=None,
        help="Rubric score ceiling override",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Path to output JSON file (defaults to stdout)",
    )
    parser.add_argument(
        "--dropped-out",
        dest="dropped_out",
        default=None,
        help="Path to write below-publish-threshold rows as JSON (PSK-008)",
    )
    parser.add_argument(
        "--unscored-out",
        dest="unscored_out",
        default=None,
        help="Path to write CATEGORISED rows (no score, tier 'unscored') as JSON; without "
        "this they ride along in --out, since they still merge into latest.json",
    )

    args = parser.parse_args(argv)

    path = Path(args.items)
    if args.items != "-" and not path.exists():
        print(f"Error: Candidate file '{path}' not found", file=sys.stderr)
        return 1
    try:
        raw_data = sys.stdin.read() if args.items == "-" else path.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        print(f"Error: the candidates input is not UTF-8 text: {err}", file=sys.stderr)
        return 1

    try:
        candidates = json.loads(raw_data)
    except json.JSONDecodeError as err:
        print(f"Error parsing candidates JSON: {err}", file=sys.stderr)
        return 1

    if not isinstance(candidates, list):
        print("Error: candidates must be a JSON array of objects", file=sys.stderr)
        return 1

    tier_a_dict = (
        {"enterprise": args.tier_a_threshold, "startup": args.tier_a_threshold}
        if args.tier_a_threshold is not None
        else None
    )
    ceilings_dict = (
        {"enterprise": args.ceiling, "startup": args.ceiling} if args.ceiling is not None else None
    )

    try:
        published, dropped, unscored = score_and_rank_prospects_with_dropped(
            candidates,
            publish_threshold=args.publish_threshold,
            tier_a_thresholds=tier_a_dict,
            ceilings=ceilings_dict,
        )
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    a_count = sum(1 for row in published if row.get("tier") == "A")
    b_count = sum(1 for row in published if row.get("tier") == "B")
    print(
        f"scored {len(candidates) - len(unscored)} · published {len(published)} "
        f"(A: {a_count}, B: {b_count}) · below threshold {len(dropped)} "
        f"· categorised {len(unscored)}",
        file=sys.stderr,
    )
    # Conservation: every candidate is in exactly one bucket. A row that leaves the denominator
    # produces a confidently wrong distribution, so this is asserted rather than assumed.
    if len(published) + len(dropped) + len(unscored) != len(candidates):
        print(
            f"Error: {len(candidates)} candidates in, "
            f"{len(published) + len(dropped) + len(unscored)} rows out — a row was lost",
            file=sys.stderr,
        )
        return 1

    # Categorised rows PROCEED (they are real ledger rows carrying their category and the input
    # they are waiting on); they are simply never ranked. They go to --out unless the caller
    # asked for them separately.
    proceeding = published if args.unscored_out else published + unscored

    # One loop, one refusal path across three outputs.
    for target, rows, what in (
        (args.out, proceeding, "scored"),
        (args.dropped_out, dropped, "dropped"),
        (args.unscored_out, unscored, "unscored"),
    ):
        if target is None and what != "scored":
            continue
        try:
            payload = json.dumps(rows, indent=2, ensure_ascii=False, allow_nan=False)
        except ValueError as err:
            print(f"Error: could not serialize {what} output: {err}", file=sys.stderr)
            return 1
        if target:
            out_path = Path(target)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(payload, encoding="utf-8")
        else:
            print(payload)

    return 0


if __name__ == "__main__":
    sys.exit(main())
