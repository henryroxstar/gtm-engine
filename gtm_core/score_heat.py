"""Intent-heat evaluation and signal-date parsing for `gtm_core.score_prospects`.

Split out of `score_prospects.py` to satisfy the §R10 file-size ratchet
(`tests/lint/complexity_check.py`) — this holds the self-contained "read untrusted
LLM-shaped intent/date data, produce a safe numeric fact" half of Step 8; `score_prospects.py`
keeps the ceiling/tier/rank/CLI half and re-exports the public name (`evaluate_heat`) from here.
No behaviour changed by the split.
"""

from __future__ import annotations

import math
import sys
from datetime import date
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

_TRUE_WORDS = frozenset({"true", "yes", "1"})

HIGH_INTENT_THRESHOLD: int = 75
ELEVATED_INTENT_MIN: int = 60

# Unicode dash variants an LLM may type in place of a plain ASCII hyphen (PSK-007).
_UNICODE_DASHES = "‐‑‒–—−"


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


def _extract_from_list(intent_data: list[Any]) -> tuple[list[str], bool, list[str]]:
    """Feed attribution for list-shaped intent data (PSK-004, PSK-005).

    An item's feed is its explicit `feed` key. An item with no `feed` key is a bare
    topic, not a feed report, so every such item shares one feed, "topic-intent" —
    two hot topics from the same feed must not fake double-intent. A bare string item
    (no dict at all) carries no score, so it earns no heat either.
    """
    feeds: list[str] = []
    topics_fired: list[str] = []
    is_elevated = False
    for item in intent_data:
        if not isinstance(item, dict):
            continue

        feed = item.get("feed") or "topic-intent"
        topic = item.get("topic")
        try:
            numeric_score = float(item.get("score", 0))
        except (ValueError, TypeError):
            numeric_score = 0.0

        if numeric_score >= HIGH_INTENT_THRESHOLD:
            feeds.append(str(feed))
            if topic:
                topics_fired.append(str(topic))
        elif numeric_score >= ELEVATED_INTENT_MIN:
            is_elevated = True
    return feeds, is_elevated, topics_fired


def _evaluate_heat_detailed(
    intent_data: Any = None,
    intent_feeds: list[str] | None = None,
    top_intent_score: int | float | None = None,
) -> tuple[int, list[str], bool, list[str], bool]:
    """Full heat evaluation, including the two extra facts `evaluate_heat` doesn't expose:
    which topics fired (for `intent_topics_fired`) and whether a feed list had no score to
    judge it by (PSK-005's `intent_unscored`)."""
    detected_high_feeds: list[str] = []
    is_elevated = False
    topics_fired: list[str] = []
    unscored = False

    if isinstance(intent_data, dict):
        detected_high_feeds, is_elevated = _extract_from_dict(intent_data)
    elif isinstance(intent_data, list):
        detected_high_feeds, is_elevated, topics_fired = _extract_from_list(intent_data)

    if not detected_high_feeds and intent_feeds:
        if top_intent_score is None:
            # PSK-005: no score means no heat — never assume a bare feed list scores 100.
            unscored = True
        elif top_intent_score >= HIGH_INTENT_THRESHOLD:
            detected_high_feeds.extend(intent_feeds)
        elif top_intent_score >= ELEVATED_INTENT_MIN:
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

    return heat, unique_feeds, is_elevated, topics_fired, unscored


def evaluate_heat(
    intent_data: Any = None,
    intent_feeds: list[str] | None = None,
    top_intent_score: int | float | None = None,
) -> tuple[int, list[str], bool]:
    """Calculate intent heat, active feeds, and elevated flag.

    Rules from gates-and-scoring.md:
    - score >= 75 on ANY feed: +2
    - Two or more DISTINCT feeds >= 75 (double-intent convergence): +1 more (total heat = 3)
    - score 60-74: elevated intent, but heat = 0
    - No score at all for a bare feed list: no heat (never assumed to score 100)
    - feeds can be passed as a dict {feed: score}, a list of {"feed"|"topic", "score"}
      dicts (an item with no `feed` key is a topic, not a feed — see `_extract_from_list`),
      or a direct intent_feeds list paired with top_intent_score.

    Returns:
        (heat: 0..3, active_feeds: list[str], is_elevated: bool)
    """
    heat, feeds, is_elevated, _topics_fired, _unscored = _evaluate_heat_detailed(
        intent_data=intent_data,
        intent_feeds=intent_feeds,
        top_intent_score=top_intent_score,
    )
    return heat, feeds, is_elevated


def _parse_signal_date_ordinal(value: Any) -> int | None:
    """Parse an ISO `YYYY-MM-DD` date into a comparable ordinal.

    Normalises LLM-typical unicode dash variants to a plain hyphen first. Never raises
    (PSK-007) — anything unparseable, including non-ASCII garbage, is just "undated".
    """
    if not value:
        return None
    text = str(value).strip()
    for dash in _UNICODE_DASHES:
        text = text.replace(dash, "-")
    try:
        return date.fromisoformat(text).toordinal()
    except (ValueError, TypeError):
        return None


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
