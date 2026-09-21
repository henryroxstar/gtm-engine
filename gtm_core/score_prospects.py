"""Deterministic scoring, heat calculation, and ranking for prospect candidates.

Implements Step 8 of the prospect skill (PRD 2026-09-20):
- Evaluates ICP rubric fit score
- Adds intent heat (+2 for intent score >= 75 on any feed; +1 more for double-intent >= 2 feeds)
- Caps total score at the rubric ceiling
- Assigns tiers (Tier A vs Tier B vs drop) and priority
- Ranks finalists by: (1) Tier A first, (2) Heat descending, (3) new_in_role, (4) Signal recency, (5) Score

Stdlib-only, deterministic, zero egress.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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
HIGH_INTENT_THRESHOLD: int = 75
ELEVATED_INTENT_MIN: int = 60


def _extract_from_dict(intent_data: dict[str, Any]) -> tuple[list[str], bool]:
    feeds: list[str] = []
    is_elevated = False
    for feed_name, score in intent_data.items():
        try:
            numeric_score = float(score) if score is not None else 0.0
        except (ValueError, TypeError):
            numeric_score = 0.0

        if numeric_score >= HIGH_INTENT_THRESHOLD:
            feeds.append(str(feed_name))
        elif numeric_score >= ELEVATED_INTENT_MIN:
            is_elevated = True
    return feeds, is_elevated


def _extract_from_list(intent_data: list[Any]) -> tuple[list[str], bool]:
    feeds: list[str] = []
    is_elevated = False
    for item in intent_data:
        if isinstance(item, dict):
            feed = item.get("feed") or item.get("topic") or "topic"
            try:
                numeric_score = float(item.get("score", 0))
            except (ValueError, TypeError):
                numeric_score = 0.0

            if numeric_score >= HIGH_INTENT_THRESHOLD:
                feeds.append(str(feed))
            elif numeric_score >= ELEVATED_INTENT_MIN:
                is_elevated = True
        elif isinstance(item, str):
            feeds.append(item)
    return feeds, is_elevated


def evaluate_heat(
    intent_data: Any = None,
    intent_feeds: list[str] | None = None,
    top_intent_score: int | float | None = None,
) -> tuple[int, list[str], bool]:
    """Calculate intent heat, active feeds, and elevated flag.

    Rules from gates-and-scoring.md:
    - score >= 75 on ANY feed: +2
    - Two or more feeds >= 75 (double-intent convergence): +1 more (total heat = 3)
    - score 60-74: elevated intent, but heat = 0
    - feeds can be passed as dict (e.g. {"vibe": 80, "rocketreach": 78}), list of dicts,
      or direct intent_feeds list with top_intent_score.

    Returns:
        (heat: 0..3, active_feeds: list[str], is_elevated: bool)
    """
    detected_high_feeds: list[str] = []
    is_elevated = False

    if isinstance(intent_data, dict):
        detected_high_feeds, is_elevated = _extract_from_dict(intent_data)
    elif isinstance(intent_data, list):
        detected_high_feeds, is_elevated = _extract_from_list(intent_data)

    if not detected_high_feeds and intent_feeds:
        score_to_check = top_intent_score if top_intent_score is not None else 100
        if score_to_check >= HIGH_INTENT_THRESHOLD:
            detected_high_feeds.extend(intent_feeds)
        elif score_to_check >= ELEVATED_INTENT_MIN:
            is_elevated = True

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_feeds: list[str] = []
    for f in detected_high_feeds:
        if f not in seen:
            seen.add(f)
            unique_feeds.append(f)

    count = len(unique_feeds)
    if count >= 2:
        heat = 3
    elif count == 1:
        heat = 2
    else:
        heat = 0

    return heat, unique_feeds, is_elevated


def score_candidate(
    candidate: dict[str, Any],
    publish_threshold: int = DEFAULT_PUBLISH_THRESHOLD,
    tier_a_threshold: int | None = None,
    ceiling: int | None = None,
) -> dict[str, Any]:
    """Score a single candidate record and assign heat, total score, tier, and priority."""
    res = dict(candidate)
    segment = (res.get("segment") or "startup").lower()

    # Determine ceiling
    if ceiling is None:
        c = DEFAULT_CEILINGS.get(segment, 10)
    else:
        c = ceiling

    # Determine Tier-A threshold
    if tier_a_threshold is None:
        t_a = DEFAULT_TIER_A_THRESHOLDS.get(segment, round(c * 0.7))
    else:
        t_a = tier_a_threshold

    # Determine base score
    base_score = res.get("base_score")
    if base_score is None:
        base_score = res.get("fit_score")
    if base_score is None:
        base_score = res.get("score", 0)
    try:
        base_score_num = float(base_score)
    except (ValueError, TypeError):
        base_score_num = 0.0

    # Evaluate heat
    intent_data = res.get("intent_scores") or res.get("intent_topics")
    intent_feeds = res.get("intent_feeds")
    top_intent_score = res.get("top_intent_score")

    heat, active_feeds, is_elevated = evaluate_heat(
        intent_data=intent_data,
        intent_feeds=intent_feeds,
        top_intent_score=top_intent_score,
    )

    # If active_feeds discovered, merge/update
    if active_feeds:
        res["intent_feeds"] = active_feeds
    elif "intent_feeds" not in res:
        res["intent_feeds"] = []

    res["heat"] = heat
    if is_elevated and heat == 0:
        res["intent_elevated"] = True

    # Calculate total score capped at ceiling
    total_score = min(base_score_num + heat, float(c))
    # If whole number, format as int
    if total_score.is_integer():
        res["score"] = int(total_score)
    else:
        res["score"] = round(total_score, 1)

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
    res["new_in_role"] = bool(res.get("new_in_role", False))

    return res


def sort_key_finalist(item: dict[str, Any]) -> tuple[int, int, int, str, float]:
    """Sort key for finalist queue:
    1. Tier priority (Tier A first, then Tier B, then drop)
    2. Heat descending (3, 2, 1, 0)
    3. new_in_role (True before False)
    4. signal_observed recency (newest ISO date first)
    5. score descending
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
    signal_date = str(item.get("signal_observed") or "")
    score = float(item.get("score", 0))

    return (
        tier_rank,
        -heat,
        -new_in_role,
        # signal_date inverted: compare negatively so later dates come first
        # Empty dates sort last
        "" if not signal_date else "".join(chr(255 - ord(ch)) for ch in signal_date),
        -score,
    )


def score_and_rank_prospects(
    candidates: list[dict[str, Any]],
    publish_threshold: int = DEFAULT_PUBLISH_THRESHOLD,
    tier_a_thresholds: dict[str, int] | None = None,
    ceilings: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Score all candidates and rank them deterministically."""
    scored: list[dict[str, Any]] = []
    for cand in candidates:
        seg = (cand.get("segment") or "startup").lower()
        t_a = None
        if tier_a_thresholds and seg in tier_a_thresholds:
            t_a = tier_a_thresholds[seg]
        ceil = None
        if ceilings and seg in ceilings:
            ceil = ceilings[seg]

        scored.append(
            score_candidate(
                cand,
                publish_threshold=publish_threshold,
                tier_a_threshold=t_a,
                ceiling=ceil,
            )
        )

    scored.sort(key=sort_key_finalist)
    return scored


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

    args = parser.parse_args(argv)

    if args.items == "-":
        raw_data = sys.stdin.read()
    else:
        path = Path(args.items)
        if not path.exists():
            print(f"Error: Candidate file '{path}' not found", file=sys.stderr)
            return 1
        raw_data = path.read_text(encoding="utf-8")

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
        if args.tier_a_threshold
        else None
    )
    ceilings_dict = {"enterprise": args.ceiling, "startup": args.ceiling} if args.ceiling else None

    ranked = score_and_rank_prospects(
        candidates,
        publish_threshold=args.publish_threshold,
        tier_a_thresholds=tier_a_dict,
        ceilings=ceilings_dict,
    )

    out_json = json.dumps(ranked, indent=2, ensure_ascii=False)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_json, encoding="utf-8")
    else:
        print(out_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
