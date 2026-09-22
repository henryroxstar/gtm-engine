"""Intent-heat evaluation and signal-date parsing for `gtm_core.score_prospects`.

Split out of `score_prospects.py` to satisfy the §R10 file-size ratchet
(`tests/lint/complexity_check.py`) — this holds the self-contained "read untrusted
LLM-shaped intent/date data, produce a safe numeric fact" half of Step 8; `score_prospects.py`
keeps the ceiling/tier/rank/CLI half and re-exports the public name (`evaluate_heat`) from here.
No behaviour changed by the split.
"""

from __future__ import annotations

from datetime import date
from typing import Any

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
